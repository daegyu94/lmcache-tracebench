from recorder.main import main


def test_mooncake_overrides_are_reflected_in_dry_run(capsys):
    assert (
        main(
            [
                "--config",
                "configs/recorder/qwen3-coder-480b-tp8-mooncake.yaml",
                "--mooncake-trace",
                "conversation",
                "--mooncake-path",
                "/tmp/conversation_trace.jsonl",
                "--mooncake-num-requests",
                "all",
                "--base-path",
                "/tmp/mooncake-{trace}",
                "--output-dir",
                "/tmp/mooncake-main-test",
                "--dry-run",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "storage.lct" in output
    assert "/tmp/mooncake-conversation" in output
    assert "--num-requests" not in output
