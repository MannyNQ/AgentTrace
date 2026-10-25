import { createClient, type ClickHouseClient } from "@clickhouse/client";
import { S3Client } from "@aws-sdk/client-s3";

// ADR-0018: the web app reads the stores directly until auth (Phase 6) needs a query API.
// Every query is scoped to the default project (infra/postgres/001_tenancy.sql).
export const DEFAULT_PROJECT_ID = "00000000-0000-0000-0000-000000000001";

// ADR-0004 / ADR-0013: a trace whose root never ended is complete after this much quiet.
export const QUIET_PERIOD_S = Number(process.env.TRACE_QUIET_PERIOD_S ?? 300);

export const S3_BUCKET = process.env.S3_BUCKET ?? "payloads";

let clickhouse: ClickHouseClient | undefined;
let s3: S3Client | undefined;

export function getClickHouse(): ClickHouseClient {
  clickhouse ??= createClient({
    url: process.env.CLICKHOUSE_URL ?? "http://localhost:8123",
    username: process.env.CLICKHOUSE_USER ?? "agenttrace",
    password: process.env.CLICKHOUSE_PASSWORD ?? "agenttrace",
    database: process.env.CLICKHOUSE_DATABASE ?? "agenttrace",
    clickhouse_settings: {
      date_time_output_format: "iso",
      output_format_json_quote_64bit_integers: 0,
    },
  });
  return clickhouse;
}

export function getS3(): S3Client {
  s3 ??= new S3Client({
    endpoint: process.env.S3_ENDPOINT ?? "http://localhost:8333",
    region: "us-east-1",
    forcePathStyle: true,
    credentials: {
      accessKeyId: process.env.S3_ACCESS_KEY ?? "agenttrace",
      secretAccessKey: process.env.S3_SECRET_KEY ?? "agenttrace-secret",
    },
  });
  return s3;
}
