"""MonitoringStack — CloudWatch Synthetics Canary, alarms, and dashboard.

Creates:
  - A Synthetics Canary that GETs /api/health every 5 minutes
  - CloudWatch Alarms for health check failures and API error rates
  - A CloudWatch Dashboard for operational visibility
"""

from aws_cdk import Duration, Stack
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_synthetics as synthetics
from constructs import Construct

# Canary Node.js inline code — hits the health endpoint
_CANARY_CODE = """
const { URL } = require('url');
const synthetics = require('Synthetics');
const log = require('SyntheticsLogger');

const apiCanaryBlueprint = async function () {
    const url = process.env.HEALTH_URL;
    log.info('Checking health at: ' + url);

    const response = await synthetics.executeHttpStep(
        'healthCheck',
        new URL(url),
        { method: 'GET' }
    );

    if (response.statusCode !== 200) {
        throw new Error('Health check failed with status: ' + response.statusCode);
    }
};

exports.handler = async () => {
    return await apiCanaryBlueprint();
};
"""


class MonitoringStack(Stack):
    """Observability: canary, alarms, and dashboard."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        domain_name: str,
        distribution_id: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        health_url = f"https://{domain_name}/api/health"

        # ── S3 bucket for canary artifacts ──────────────────────────
        canary_bucket = s3.Bucket(
            self,
            "CanaryArtifacts",
            bucket_name=f"acme-dental-canary-{self.account}",
            auto_delete_objects=True,
            removal_policy=Stack.of(self).removal_policy
            if hasattr(Stack.of(self), "removal_policy")
            else None,
        )

        # ── Synthetics Canary ───────────────────────────────────────
        canary = synthetics.Canary(
            self,
            "HealthCanary",
            canary_name="acme-dental-health",
            runtime=synthetics.Runtime.SYNTHETICS_NODEJS_PUPPETEER_9_1,
            test=synthetics.Test.custom(
                code=synthetics.Code.from_inline(_CANARY_CODE),
                handler="index.handler",
            ),
            schedule=synthetics.Schedule.rate(Duration.minutes(5)),
            artifacts_bucket_location=synthetics.ArtifactsBucketLocation(
                bucket=canary_bucket,
            ),
            environment_variables={
                "HEALTH_URL": health_url,
            },
        )

        # ── CloudWatch Alarms ───────────────────────────────────────

        # 1. Health check canary failure
        canary_alarm = cloudwatch.Alarm(
            self,
            "HealthCheckAlarm",
            alarm_name="AcmeDental-HealthCheckFailing",
            metric=cloudwatch.Metric(
                namespace="CloudWatchSynthetics",
                metric_name="SuccessPercent",
                dimensions_map={"CanaryName": "acme-dental-health"},
                period=Duration.minutes(5),
                statistic="Average",
            ),
            threshold=100,
            comparison_operator=cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
            evaluation_periods=2,
            treat_missing_data=cloudwatch.TreatMissingData.BREACHING,
            alarm_description="Health check canary failed for 2 consecutive periods",
        )

        # 2. Calendly API error rate
        calendly_error_alarm = cloudwatch.Alarm(
            self,
            "CalendlyErrorAlarm",
            alarm_name="AcmeDental-CalendlyHighErrorRate",
            metric=cloudwatch.Metric(
                namespace="AcmeDental",
                metric_name="ExternalAPI/ErrorCount",
                dimensions_map={"Service": "calendly"},
                period=Duration.minutes(5),
                statistic="Sum",
            ),
            threshold=5,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            evaluation_periods=1,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description="More than 5 Calendly API errors in 5 minutes",
        )

        # 3. Anthropic API error rate
        anthropic_error_alarm = cloudwatch.Alarm(
            self,
            "AnthropicErrorAlarm",
            alarm_name="AcmeDental-AnthropicHighErrorRate",
            metric=cloudwatch.Metric(
                namespace="AcmeDental",
                metric_name="ExternalAPI/ErrorCount",
                dimensions_map={"Service": "anthropic"},
                period=Duration.minutes(5),
                statistic="Sum",
            ),
            threshold=5,
            comparison_operator=cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
            evaluation_periods=1,
            treat_missing_data=cloudwatch.TreatMissingData.NOT_BREACHING,
            alarm_description="More than 5 Anthropic API errors in 5 minutes",
        )

        # ── CloudWatch Dashboard ────────────────────────────────────
        dashboard = cloudwatch.Dashboard(
            self,
            "Dashboard",
            dashboard_name="AcmeDental-Operations",
        )

        # Row 1: Health and request counts
        dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="Health Check Success %",
                left=[
                    cloudwatch.Metric(
                        namespace="CloudWatchSynthetics",
                        metric_name="SuccessPercent",
                        dimensions_map={"CanaryName": "acme-dental-health"},
                        period=Duration.minutes(5),
                        statistic="Average",
                    ),
                ],
                width=8,
            ),
            cloudwatch.GraphWidget(
                title="API Request Count (by service)",
                left=[
                    cloudwatch.Metric(
                        namespace="AcmeDental",
                        metric_name="ExternalAPI/RequestCount",
                        dimensions_map={"Service": "calendly", "Status": "success"},
                        period=Duration.minutes(5),
                        statistic="Sum",
                        label="Calendly Success",
                    ),
                    cloudwatch.Metric(
                        namespace="AcmeDental",
                        metric_name="ExternalAPI/RequestCount",
                        dimensions_map={"Service": "anthropic", "Status": "success"},
                        period=Duration.minutes(5),
                        statistic="Sum",
                        label="Anthropic Success",
                    ),
                ],
                width=8,
            ),
            cloudwatch.GraphWidget(
                title="API Error Count (by service)",
                left=[
                    cloudwatch.Metric(
                        namespace="AcmeDental",
                        metric_name="ExternalAPI/ErrorCount",
                        dimensions_map={"Service": "calendly"},
                        period=Duration.minutes(5),
                        statistic="Sum",
                        label="Calendly Errors",
                    ),
                    cloudwatch.Metric(
                        namespace="AcmeDental",
                        metric_name="ExternalAPI/ErrorCount",
                        dimensions_map={"Service": "anthropic"},
                        period=Duration.minutes(5),
                        statistic="Sum",
                        label="Anthropic Errors",
                    ),
                ],
                width=8,
            ),
        )

        # Row 2: Latency
        dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="Calendly API Latency (P50 / P95 / P99)",
                left=[
                    cloudwatch.Metric(
                        namespace="AcmeDental",
                        metric_name="ExternalAPI/Latency",
                        dimensions_map={"Service": "calendly"},
                        period=Duration.minutes(5),
                        statistic="p50",
                        label="P50",
                    ),
                    cloudwatch.Metric(
                        namespace="AcmeDental",
                        metric_name="ExternalAPI/Latency",
                        dimensions_map={"Service": "calendly"},
                        period=Duration.minutes(5),
                        statistic="p95",
                        label="P95",
                    ),
                    cloudwatch.Metric(
                        namespace="AcmeDental",
                        metric_name="ExternalAPI/Latency",
                        dimensions_map={"Service": "calendly"},
                        period=Duration.minutes(5),
                        statistic="p99",
                        label="P99",
                    ),
                ],
                width=12,
            ),
            cloudwatch.GraphWidget(
                title="Anthropic LLM Latency (P50 / P95 / P99)",
                left=[
                    cloudwatch.Metric(
                        namespace="AcmeDental",
                        metric_name="ExternalAPI/Latency",
                        dimensions_map={"Service": "anthropic"},
                        period=Duration.minutes(5),
                        statistic="p50",
                        label="P50",
                    ),
                    cloudwatch.Metric(
                        namespace="AcmeDental",
                        metric_name="ExternalAPI/Latency",
                        dimensions_map={"Service": "anthropic"},
                        period=Duration.minutes(5),
                        statistic="p95",
                        label="P95",
                    ),
                    cloudwatch.Metric(
                        namespace="AcmeDental",
                        metric_name="ExternalAPI/Latency",
                        dimensions_map={"Service": "anthropic"},
                        period=Duration.minutes(5),
                        statistic="p99",
                        label="P99",
                    ),
                ],
                width=12,
            ),
        )
