import pytest


pytestmark = pytest.mark.smoke


def test_os_release_exists(host):
    os_release = host.file("/etc/os-release")

    assert os_release.exists
    assert os_release.is_file
    assert os_release.contains("Debian")


def test_root_user_exists(host):
    root = host.user("root")

    assert root.exists
    assert root.uid == 0


def test_command_execution(host):
    result = host.run("printf testinfra-from-salt")

    assert result.rc == 0
    assert result.stdout == "testinfra-from-salt"
