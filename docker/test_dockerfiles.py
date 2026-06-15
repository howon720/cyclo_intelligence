from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_main_dockerfiles_install_compose_v2():
    for dockerfile in (
        REPO_ROOT / "docker" / "Dockerfile.arm64",
        REPO_ROOT / "docker" / "Dockerfile.amd64",
    ):
        contents = dockerfile.read_text()

        assert "docker-compose-v2" in contents, (
            f"{dockerfile.name} must install docker-compose-v2 so "
            "supervisor_api can recreate policy containers with docker compose"
        )
