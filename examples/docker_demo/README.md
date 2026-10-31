# Docker demo: validate a live document database

```bash
docker compose -f examples/docker_demo/docker-compose.yml up --build --abort-on-container-exit
```

1. `pipeline` generates the synthetic EBCDIC member file, runs the reference transform and loads
   the documents into MongoDB, standing in for Amazon DocumentDB.
2. `mapverity` reads the source files and the live collection through the `docdb` reader and
   validates them against the mapping sheet with a coverage gate.
