"""Tests for secret_manager.py."""

from __future__ import annotations

from patterns.secret_manager import (
    ProviderType,
    SecretConfig,
    SecretManager,
    SecretScope,
    SecretType,
)


class TestSecretConfig:
    def test_has_credentials_true(self):
        cfg = SecretConfig(name="s", key_id="AKID", secret="SECRET")
        assert cfg.has_credentials() is True

    def test_has_credentials_false(self):
        cfg = SecretConfig(name="s")
        assert cfg.has_credentials() is False

    def test_is_persistent_false(self):
        cfg = SecretConfig(name="s", scope=SecretScope.SESSION)
        assert cfg.is_persistent() is False

    def test_is_persistent_true(self):
        cfg = SecretConfig(name="s", scope=SecretScope.PERMANENT)
        assert cfg.is_persistent() is True

    def test_redacted_hides_key(self):
        cfg = SecretConfig(name="s", key_id="REAL_KEY", secret="REAL_SECRET")
        r = cfg.redacted()
        assert r.key_id == "***"
        assert r.secret == "***"

    def test_redacted_preserves_name(self):
        cfg = SecretConfig(name="my_secret", key_id="K", secret="S")
        assert cfg.redacted().name == "my_secret"

    def test_redacted_empty_stays_empty(self):
        cfg = SecretConfig(name="s")
        r = cfg.redacted()
        assert r.key_id == ""
        assert r.secret == ""


class TestSecretManager:
    def setup_method(self):
        self.mgr = SecretManager()

    def test_add_and_get(self):
        cfg = SecretConfig(name="my_s3", secret_type=SecretType.S3)
        self.mgr.add(cfg)
        assert self.mgr.get("my_s3") is cfg

    def test_get_missing(self):
        assert self.mgr.get("missing") is None

    def test_names(self):
        self.mgr.add(SecretConfig(name="a"))
        self.mgr.add(SecretConfig(name="b"))
        assert set(self.mgr.names()) == {"a", "b"}

    def test_create_secret_sql_type(self):
        cfg = SecretConfig(name="s3_key", secret_type=SecretType.S3)
        sql = self.mgr.create_secret_sql(cfg)
        assert "CREATE SECRET s3_key" in sql
        assert "TYPE S3" in sql

    def test_create_secret_sql_with_credentials(self):
        cfg = SecretConfig(
            name="s3_cfg",
            secret_type=SecretType.S3,
            provider=ProviderType.CONFIG,
            key_id="AKID",
            secret="SECRET",
            region="us-east-1",
        )
        sql = self.mgr.create_secret_sql(cfg)
        assert "KEY_ID 'AKID'" in sql
        assert "SECRET 'SECRET'" in sql
        assert "REGION 'us-east-1'" in sql

    def test_create_secret_sql_persistent(self):
        cfg = SecretConfig(name="s", scope=SecretScope.PERMANENT)
        sql = self.mgr.create_secret_sql(cfg)
        assert "PERSISTENT" in sql

    def test_create_secret_sql_session(self):
        cfg = SecretConfig(name="s", scope=SecretScope.SESSION)
        sql = self.mgr.create_secret_sql(cfg)
        assert "PERSISTENT" not in sql

    def test_create_secret_with_endpoint(self):
        cfg = SecretConfig(name="r2", secret_type=SecretType.R2, endpoint="r2.example.com")
        sql = self.mgr.create_secret_sql(cfg)
        assert "ENDPOINT 'r2.example.com'" in sql

    def test_create_secret_with_session_token(self):
        cfg = SecretConfig(
            name="s",
            provider=ProviderType.CONFIG,
            key_id="K",
            secret="S",
            session_token="TOKEN",
        )
        sql = self.mgr.create_secret_sql(cfg)
        assert "SESSION_TOKEN 'TOKEN'" in sql

    def test_create_secret_or_replace(self):
        cfg = SecretConfig(name="s")
        self.mgr.add(cfg)
        sql = self.mgr.create_secret_sql(cfg)
        assert "OR REPLACE" in sql

    def test_create_secret_no_or_replace_for_new(self):
        cfg = SecretConfig(name="new_secret")
        sql = self.mgr.create_secret_sql(cfg)
        assert "OR REPLACE" not in sql

    def test_drop_secret_sql(self):
        sql = self.mgr.drop_secret_sql("my_s3")
        assert "DROP SECRET" in sql
        assert "my_s3" in sql

    def test_drop_secret_if_exists(self):
        assert "IF EXISTS" in self.mgr.drop_secret_sql("s", if_exists=True)

    def test_drop_secret_no_if_exists(self):
        assert "IF EXISTS" not in self.mgr.drop_secret_sql("s", if_exists=False)

    def test_create_all_sql(self):
        self.mgr.add(SecretConfig(name="a"))
        self.mgr.add(SecretConfig(name="b"))
        stmts = self.mgr.create_all_sql()
        assert len(stmts) == 2

    def test_s3_from_env(self):
        sql = self.mgr.s3_from_env()
        assert "credential_chain" in sql.lower()

    def test_credential_chain_no_key_secret(self):
        cfg = SecretConfig(name="chain", provider=ProviderType.CREDENTIAL_CHAIN)
        sql = self.mgr.create_secret_sql(cfg)
        assert "KEY_ID" not in sql

    def test_extra_params(self):
        cfg = SecretConfig(name="s", extra_params={"use_ssl": "true"})
        sql = self.mgr.create_secret_sql(cfg)
        assert "USE_SSL 'true'" in sql
