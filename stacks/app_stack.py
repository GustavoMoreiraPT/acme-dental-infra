"""AppStack — VPC, ECS Fargate, ALB, S3, CloudFront, Route 53 records.

This is the core application infrastructure.  It creates:
  - A VPC with 2 public subnets (no NAT Gateway to save cost)
  - An ECS Fargate service running the FastAPI backend
  - An ALB in front of the ECS service
  - An S3 bucket for the React frontend
  - A CloudFront distribution with path-based routing
  - Route 53 alias records pointing the domain to CloudFront
"""

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_cloudfront as cloudfront
from aws_cdk import aws_cloudfront_origins as origins
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr as ecr
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_ecs_patterns as ecs_patterns
from aws_cdk import aws_iam as iam
from aws_cdk import aws_route53 as route53
from aws_cdk import aws_route53_targets as targets
from aws_cdk import aws_s3 as s3
from constructs import Construct


class AppStack(Stack):
    """Core application infrastructure."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        domain_name: str,
        hosted_zone: route53.IHostedZone,
        certificate: object,  # acm.ICertificate
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── VPC (2 public subnets, no NAT to save cost) ────────────
        vpc = ec2.Vpc(
            self,
            "Vpc",
            max_azs=2,
            nat_gateways=0,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
            ],
        )

        # ── ECR Repository (created outside CDK to avoid chicken-and-egg
        #    on first deploy — ECS needs an image before the service
        #    can stabilise, but the repo must exist to push the image) ─
        self.ecr_repo = ecr.Repository.from_repository_name(
            self,
            "BackendRepo",
            repository_name="acme-dental-backend",
        )

        # ── ECS Cluster ─────────────────────────────────────────────
        cluster = ecs.Cluster(
            self,
            "Cluster",
            vpc=vpc,
            cluster_name="acme-dental",
        )

        # ── Task Definition ─────────────────────────────────────────
        task_def = ecs.FargateTaskDefinition(
            self,
            "TaskDef",
            cpu=256,
            memory_limit_mib=512,
        )

        # Grant the task role access to CloudWatch metrics + SSM secrets
        task_def.task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["cloudwatch:PutMetricData"],
                resources=["*"],
            )
        )
        task_def.task_role.add_to_policy(
            iam.PolicyStatement(
                actions=["ssm:GetParameter"],
                resources=[
                    f"arn:aws:ssm:{self.region}:{self.account}:parameter/acme-dental/*",
                ],
            )
        )

        # ── Container ───────────────────────────────────────────────
        container = task_def.add_container(
            "Backend",
            image=ecs.ContainerImage.from_ecr_repository(self.ecr_repo, tag="latest"),
            logging=ecs.LogDrivers.aws_logs(stream_prefix="acme-dental"),
            environment={
                "METRICS_ENABLED": "true",
                "AWS_EXECUTION_ENV": "ECS_FARGATE",
                "SERVER_HOST": "0.0.0.0",
                "SERVER_PORT": "8000",
            },
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')\""],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                retries=3,
                start_period=Duration.seconds(15),
            ),
        )
        container.add_port_mappings(ecs.PortMapping(container_port=8000))

        # ── ALB + Fargate Service ───────────────────────────────────
        fargate_service = ecs_patterns.ApplicationLoadBalancedFargateService(
            self,
            "FargateService",
            cluster=cluster,
            task_definition=task_def,
            desired_count=1,
            assign_public_ip=True,  # public subnet, no NAT needed
            listener_port=80,
            public_load_balancer=True,
        )

        # Healthy threshold tuning
        fargate_service.target_group.configure_health_check(
            path="/api/health",
            healthy_http_codes="200",
            interval=Duration.seconds(30),
            timeout=Duration.seconds(5),
        )

        self.alb = fargate_service.load_balancer

        # ── S3 Bucket for React SPA ─────────────────────────────────
        self.frontend_bucket = s3.Bucket(
            self,
            "FrontendBucket",
            bucket_name=f"acme-dental-ui-{self.account}",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )

        # ── CloudFront Distribution ─────────────────────────────────
        # Origin 1: S3 (React SPA)
        s3_origin = origins.S3BucketOrigin.with_origin_access_control(
            self.frontend_bucket,
        )

        # Origin 2: ALB (FastAPI backend)
        alb_origin = origins.HttpOrigin(
            self.alb.load_balancer_dns_name,
            protocol_policy=cloudfront.OriginProtocolPolicy.HTTP_ONLY,
        )

        self.distribution = cloudfront.Distribution(
            self,
            "Distribution",
            domain_names=[domain_name],
            certificate=certificate,
            default_root_object="index.html",
            # Default behavior: S3 (React SPA)
            default_behavior=cloudfront.BehaviorOptions(
                origin=s3_origin,
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            ),
            # API behavior: forward to ALB
            additional_behaviors={
                "/api/*": cloudfront.BehaviorOptions(
                    origin=alb_origin,
                    viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                    allowed_methods=cloudfront.AllowedMethods.ALLOW_ALL,
                    cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
                    origin_request_policy=cloudfront.OriginRequestPolicy.ALL_VIEWER,
                ),
            },
            # SPA fallback: serve index.html for all 404s from S3
            error_responses=[
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_page_path="/index.html",
                    response_http_status=200,
                    ttl=Duration.seconds(0),
                ),
                cloudfront.ErrorResponse(
                    http_status=404,
                    response_page_path="/index.html",
                    response_http_status=200,
                    ttl=Duration.seconds(0),
                ),
            ],
        )

        # ── Route 53 alias records ──────────────────────────────────
        route53.ARecord(
            self,
            "AliasA",
            zone=hosted_zone,
            target=route53.RecordTarget.from_alias(
                targets.CloudFrontTarget(self.distribution),
            ),
        )
        route53.AaaaRecord(
            self,
            "AliasAAAA",
            zone=hosted_zone,
            target=route53.RecordTarget.from_alias(
                targets.CloudFrontTarget(self.distribution),
            ),
        )

        # ── Outputs ─────────────────────────────────────────────────
        CfnOutput(self, "AlbDns", value=self.alb.load_balancer_dns_name)
        CfnOutput(self, "CloudFrontDomain", value=self.distribution.distribution_domain_name)
        CfnOutput(self, "EcrRepoUri", value=self.ecr_repo.repository_uri)
        CfnOutput(self, "FrontendBucketName", value=self.frontend_bucket.bucket_name)
        CfnOutput(
            self,
            "DistributionId",
            value=self.distribution.distribution_id,
            description="CloudFront distribution ID (needed for cache invalidation in CI/CD)",
        )
