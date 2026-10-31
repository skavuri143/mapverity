"""AWS Glue (PySpark) job: member master (EBCDIC, copybook) + eligibility segments (pipe-delimited)
from S3  ->  member JSON documents in Amazon DocumentDB.

This is the job mapverity tests. It reads the mainframe file with the open-source Cobrix
spark-cobol data source (add its JAR via --extra-jars) and writes through Glue's DocumentDB
connection. `transform.py` mirrors this logic in pandas for local runs.

Job parameters: --JOB_NAME --landing_path --copybook_path --docdb_secret_id
                --target_database --target_collection
Not executed by mapverity's unit tests (it needs a Glue runtime). In CI it is exercised by the
integration stage of ci/aws/pipeline.yaml, which runs it on fixture data and validates the output.
"""

import json
import sys

import boto3
from awsglue.context import GlueContext
from awsglue.dynamicframe import DynamicFrame
from awsglue.job import Job
from awsglue.utils import getResolvedOptions
from pyspark.context import SparkContext
from pyspark.sql import functions as F

args = getResolvedOptions(
    sys.argv, ["JOB_NAME", "landing_path", "copybook_path", "docdb_secret_id", "target_database", "target_collection"]
)
sc = SparkContext()
glue = GlueContext(sc)
spark = glue.spark_session
job = Job(glue)
job.init(args["JOB_NAME"], args)

landing = args["landing_path"].rstrip("/")

# 1. Source: mainframe member master (EBCDIC, COMP-3) via Cobrix
master = (
    spark.read.format("cobol")
    .option("copybook", args["copybook_path"])
    .option("encoding", "ebcdic")
    .option("schema_retention_policy", "collapse_root")
    .load(f"{landing}/MEMBER.MASTER.dat")
)

# 2. Source: eligibility segments (pipe-delimited text)
elig = spark.read.option("header", True).option("sep", "|").csv(f"{landing}/ELIG_SEGMENTS.txt")

gender = F.create_map(F.lit("M"), F.lit("MALE"), F.lit("F"), F.lit("FEMALE"))
status = F.create_map(F.lit("A"), F.lit("ACTIVE"), F.lit("P"), F.lit("PENDING"))
plans = {
    "FFS": "FEE_FOR_SERVICE",
    "MCO1": "HEALTH_PLAN_A",
    "MCO2": "HEALTH_PLAN_B",
    "MCO3": "HEALTH_PLAN_C",
    "MCO4": "HEALTH_PLAN_D",
    "MCO5": "HEALTH_PLAN_E",
}
plan = F.create_map(*[F.lit(x) for kv in plans.items() for x in kv])


def iso(col, fmt="yyyyMMdd"):
    return F.date_format(F.to_date(F.trim(col.cast("string")), fmt), "yyyy-MM-dd")


# 3. Transform per the data-model mapping (mapping/member_data_model_mapping.csv)
m = (
    master.filter(F.trim("MBR_STATUS_CD").isin("A", "P"))
    .withColumn("first", F.initcap(F.trim("MBR_FIRST_NAME")))
    .withColumn("last", F.initcap(F.trim("MBR_LAST_NAME")))
    .withColumn("phone_raw", F.trim("MBR_PHONE"))
)
fpl = F.round(F.col("MBR_MONTHLY_INCOME") * 12 / (15060 + 5380 * (F.col("MBR_HH_SIZE") - 1)) * 100, 1)

segments = (
    elig.withColumn("MBR_ID", F.trim("MBR_ID"))
    .groupBy("MBR_ID")
    .agg(
        F.sort_array(
            F.collect_list(
                F.struct(
                    F.col("SEG_SEQ").cast("int").alias("seq"),
                    F.upper(F.trim("AID_CAT")).alias("aidCategory"),
                    iso(F.col("START_DT")).alias("startDate"),
                    iso(F.col("END_DT")).alias("endDate"),
                    plan[F.trim("PLAN_CD")].alias("planCode"),
                )
            )
        ).alias("eligibility")
    )
)

docs = (
    m.withColumn("MBR_ID", F.trim("MBR_ID"))
    .join(segments, "MBR_ID", "left")
    .select(
        F.col("MBR_ID").alias("_id"),
        F.col("MBR_ID").alias("memberId"),
        F.struct(F.col("first"), F.col("last"), F.concat_ws(" ", "first", "last").alias("full")).alias("name"),
        iso(F.col("MBR_DOB")).alias("birthDate"),
        F.coalesce(gender[F.trim("MBR_GENDER_CD")], F.lit("UNKNOWN")).alias("gender"),
        F.lpad(F.trim("MBR_COUNTY_CD"), 2, "0").alias("countyCode"),
        F.col("MBR_HH_SIZE").cast("int").alias("householdSize"),
        F.round(F.col("MBR_MONTHLY_INCOME").cast("double"), 2).alias("monthlyIncome"),
        fpl.cast("double").alias("fplPercent"),
        status[F.trim("MBR_STATUS_CD")].alias("status"),
        F.struct(
            F.when(
                F.length("phone_raw") == 10,
                F.concat_ws(
                    "-", F.substring("phone_raw", 1, 3), F.substring("phone_raw", 4, 3), F.substring("phone_raw", 7, 4)
                ),
            ).alias("phone")
        ).alias("contact"),
        iso(F.col("MBR_LAST_UPDT")).alias("lastUpdated"),
        F.struct(
            F.lit("LEGACY").alias("system"),
            F.date_format(F.current_timestamp(), "yyyy-MM-dd'T'HH:mm:ss'Z'").alias("loadTimestamp"),
        ).alias("source"),
        F.coalesce(F.col("eligibility"), F.array()).alias("eligibility"),
    )
)

# 4. Target: DocumentDB
secret = json.loads(boto3.client("secretsmanager").get_secret_value(SecretId=args["docdb_secret_id"])["SecretString"])
glue.write_dynamic_frame.from_options(
    frame=DynamicFrame.fromDF(docs, glue, "members"),
    connection_type="documentdb",
    connection_options={
        "uri": f"mongodb://{secret['host']}:{secret.get('port', 27017)}",
        "database": args["target_database"],
        "collection": args["target_collection"],
        "username": secret["username"],
        "password": secret["password"],
        "ssl": "true",
        "ssl.domain_match": "false",
        "partitioner": "MongoSamplePartitioner",
    },
)
job.commit()
