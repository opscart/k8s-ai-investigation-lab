from investigator.anchors import log_anchors

LIVE_LINE = "java.lang.IllegalStateException: startup dependency configuration invalid"


def labels(text):
    return {anchor.label() for anchor in log_anchors(text)}


def test_java_runtime_exception_is_not_a_class_anchor():
    found = labels(LIVE_LINE)
    assert not any("IllegalStateException" in label for label in found)
    assert "class_name:lang.IllegalStateException" not in found


def test_library_names_with_prefixes_are_excluded_but_application_names_are_kept():
    text = "\n".join(
        [
            LIVE_LINE,
            "org.junit.jupiter.api.AssertionFailedError: x",
            "com.opscart.lab.BillingConfigException: bad",
            "[ERROR] com.opscart.lab.DependencyConfigTest.startsWithConfiguredDependency",
        ]
    )
    found = labels(text)
    assert "class_name:com.opscart.lab.BillingConfigException" in found
    assert "test_name:com.opscart.lab.DependencyConfigTest" in found
    assert not any(
        label.endswith(("IllegalStateException", "AssertionFailedError")) for label in found
    )
