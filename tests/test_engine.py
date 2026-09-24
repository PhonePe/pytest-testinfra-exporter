from pytest_testinfra_exporter.engine import create_backend_url, redacted_url


class Config:
    def __init__(self, values):
        self.values = values

    def getoption(self, name, default=None):
        return self.values.get(name, default)


def test_url_escapes_credentials_and_redacts_password():
    config = Config(
        {
            "--datastore-config": "/does/not/exist",
            "--postgres-host": "db.example",
            "--postgres-port": 5432,
            "--postgres-user": "reporter",
            "--postgres-password": "p@ss:/word",
            "--postgres-database": "reports",
        }
    )

    url = create_backend_url(config, "postgres")

    assert url.password == "p@ss:/word"
    assert "p%40ss%3A%2Fword" in url.render_as_string(hide_password=False)
    assert "p@ss:/word" not in redacted_url(url)
    assert "***" in redacted_url(url)
