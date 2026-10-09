package com.opscart.lab;

public final class DependencyConfig {
    private DependencyConfig() {
    }

    public static String billingApiUrl() {
        String value = System.getProperty("billing.api.url");
        if (value == null || value.isBlank()) {
            throw new IllegalStateException("startup dependency configuration invalid");
        }
        return value;
    }
}
