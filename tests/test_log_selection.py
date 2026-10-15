from investigator.log_selection import select_failure_log


def test_select_failure_log_keeps_explainable_failure_windows_and_drops_noise():
    text = "\n".join(
        [
            "unrelated setup",
            "Downloading from central: artifact",
            "Progress (1): 50/100 kB",
            "[INFO] Running com.opscart.lab.DependencyConfigTest",
            "[ERROR] Tests run: 1, Failures: 0, Errors: 1",
            "java.lang.IllegalStateException: startup dependency configuration invalid",
            "    at com.opscart.lab.DependencyConfig.billingApiUrl(DependencyConfig.java:10)",
            "[ERROR] BUILD FAILURE",
            "##[error]Bash exited with code '1'.",
        ]
    )

    selected = select_failure_log(text, line_limit=100, char_limit=8000)

    assert "Downloading from central" not in selected.text
    assert "Progress (1)" not in selected.text
    assert "L6: java.lang.IllegalStateException" in selected.text
    assert "L7:     at com.opscart.lab.DependencyConfig" in selected.text
    assert "L9: ##[error]Bash exited with code '1'." in selected.text
    assert selected.reasons["azure_error"] == 1
    assert selected.reasons["error_marker"] == 3
    assert selected.reasons["exception"] == 1
    assert selected.reasons["stack_frame"] == 1
    assert selected.selected_lines <= 100
    assert selected.selected_chars <= 8000


def test_select_failure_log_uses_bounded_tail_when_no_anchor_exists():
    selected = select_failure_log("line one\nline two\nline three", line_limit=2, char_limit=100)
    assert selected.text == "L2: line two\nL3: line three"
    assert selected.reasons == {"bounded_tail_fallback": 1}
