# Synthetic Java application pipeline

This directory represents the application repository in the Azure DevOps
cross-repository experiment. Its pipeline references the separate synthetic shared
library and intentionally supplies the wrong JVM property name. The first CI build is
expected to fail during Maven tests with a generic configuration error.

Run either check locally from this directory:

```bash
mvn test -Dbilling.service.url=http://billing-api.invalid
mvn test -Dbilling.api.url=http://billing-api.invalid
```

The first command fails. The second passes. The application source defines the required
property, while the shared job template controls how the pipeline passes the supplied
property to Maven.
