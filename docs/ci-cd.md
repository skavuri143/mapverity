# CI/CD

MapVerity is a command with an exit code and JUnit output, so it fits any CI system.

## GitHub Actions

```yaml
- run: pip install "mapverity[aws]"
- run: mapverity mutate member.mapverity.yaml --dataset members --json mutation.json --min-score 0.9
- run: >
    mapverity validate member.mapverity.yaml --mapping mapping.csv --mutation-report mutation.json
    --gates gates.yaml --junit results.xml --html report.html
- uses: actions/upload-artifact@v4
  if: always()
  with: {name: mapverity-report, path: report.html}
```

## AWS CodePipeline

`ci/aws/` contains a CloudFormation template and CodeBuild buildspecs for a pipeline that runs on
every change to the Glue job, mapping sheet, copybooks or tests:

1. **Unit:** framework tests, the mapping compiled and run locally on synthetic data, a mutation gate.
2. **Deploy to test:** publish the Glue script.
3. **Integration:** synthetic fixtures to an isolated S3 prefix, run the real Glue job, validate the
   DocumentDB collection against the mapping, JUnit into CodeBuild Reports.
4. **Approve:** manual promotion.

See `ci/aws/README.md`. Deploy it to a test account first.

## Jenkins / Azure DevOps

```groovy
sh 'mapverity validate member.mapverity.yaml --gates gates.yaml --junit results.xml --allure allure-results'
junit 'results.xml'
allure includeProperties: false, results: [[path: 'allure-results']]
```

```yaml
- script: mapverity validate member.mapverity.yaml --gates gates.yaml --junit $(Build.ArtifactStagingDirectory)/results.xml
- task: PublishTestResults@2
  inputs: {testResultsFormat: JUnit, testResultsFiles: "$(Build.ArtifactStagingDirectory)/results.xml"}
```

## Prioritized runs

```bash
mapverity validate member.mapverity.yaml --prioritize --history .mapverity/history.json \
    --changed members.gender --fail-fast
```

Cache `.mapverity/history.json` between runs (actions/cache, S3) so ordering learns from history.
