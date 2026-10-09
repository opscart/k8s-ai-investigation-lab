package com.opscart.lab;

import static org.junit.jupiter.api.Assertions.assertFalse;

import org.junit.jupiter.api.Test;

class DependencyConfigTest {
    @Test
    void startsWithConfiguredDependency() {
        assertFalse(DependencyConfig.billingApiUrl().isBlank());
    }
}
