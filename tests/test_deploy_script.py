import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / ".github" / "scripts" / "deploy-dokploy.sh"


def _deployment_env() -> dict[str, str]:
    return {
        **os.environ,
        "DOKPLOY_HOST": "https://dokploy.example.com",
        "DOKPLOY_TOKEN": "test-api-token",
        "DOKPLOY_APP_ID": "test-application",
        "DOKPLOY_IMAGE": "ghcr.io/example/garmin-sheets-sync:1.2.3",
        "DOKPLOY_REGISTRY_URL": "ghcr.io",
        "DOKPLOY_REGISTRY_USERNAME": "test-registry-user",
        "DOKPLOY_REGISTRY_PASSWORD": "test-registry-password",
    }


def test_deploy_script_rejects_plaintext_http() -> None:
    environment = _deployment_env()
    environment["DOKPLOY_HOST"] = "http://dokploy.example.com"

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "must use HTTPS" in result.stderr


def test_deploy_script_waits_for_completion_without_logging_secrets(tmp_path: Path) -> None:
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    counter = tmp_path / "poll-count"
    fake_curl = bin_directory / "curl"
    fake_curl.write_text(
        """#!/bin/sh
case " $* " in
  *application.one*)
    count=0
    if [ -f "$FAKE_CURL_COUNTER" ]; then
      count="$(cat "$FAKE_CURL_COUNTER")"
    fi
    count=$((count + 1))
    printf '%s' "$count" > "$FAKE_CURL_COUNTER"
    if [ "$count" -eq 1 ]; then
      status=done
      deployment_id=previous
    elif [ "$count" -eq 2 ]; then
      status=running
      deployment_id=current
    else
      status=done
      deployment_id=current
    fi
    printf '{"applicationStatus":"%s","dockerImage":"%s",' \
      "$status" "$DOKPLOY_IMAGE"
    printf '"deployments":[{"deploymentId":"%s",' "$deployment_id"
    printf '"createdAt":"2026-08-10T19:00:00Z","status":"%s"}]}\n' "$status"
    ;;
esac
"""
    )
    fake_curl.chmod(0o755)
    fake_sleep = bin_directory / "sleep"
    fake_sleep.write_text("#!/bin/sh\nexit 0\n")
    fake_sleep.chmod(0o755)
    environment = _deployment_env()
    environment["FAKE_CURL_COUNTER"] = str(counter)
    environment["PATH"] = f"{bin_directory}:{environment['PATH']}"

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode == 0
    assert "Dokploy deployment completed" in output
    assert counter.read_text() == "3"
    assert environment["DOKPLOY_TOKEN"] not in output
    assert environment["DOKPLOY_REGISTRY_PASSWORD"] not in output


def test_deploy_script_accepts_a_new_deployment_that_finishes_before_first_poll(
    tmp_path: Path,
) -> None:
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    counter = tmp_path / "poll-count"
    fake_curl = bin_directory / "curl"
    fake_curl.write_text(
        """#!/bin/sh
case " $* " in
  *application.one*)
    count=0
    if [ -f "$FAKE_CURL_COUNTER" ]; then
      count="$(cat "$FAKE_CURL_COUNTER")"
    fi
    count=$((count + 1))
    printf '%s' "$count" > "$FAKE_CURL_COUNTER"
    if [ "$count" -eq 1 ]; then
      deployment_id=previous
    else
      deployment_id=current
    fi
    printf '{"applicationStatus":"done","dockerImage":"%s",' "$DOKPLOY_IMAGE"
    printf '"deployments":[{"deploymentId":"%s",' "$deployment_id"
    printf '"createdAt":"2026-08-10T19:00:00Z","status":"done"}]}\n'
    ;;
esac
"""
    )
    fake_curl.chmod(0o755)
    fake_sleep = bin_directory / "sleep"
    fake_sleep.write_text("#!/bin/sh\nexit 0\n")
    fake_sleep.chmod(0o755)
    environment = _deployment_env()
    environment["FAKE_CURL_COUNTER"] = str(counter)
    environment["PATH"] = f"{bin_directory}:{environment['PATH']}"

    result = subprocess.run(
        ["bash", str(SCRIPT)],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Dokploy deployment completed" in result.stdout
    assert counter.read_text() == "2"
