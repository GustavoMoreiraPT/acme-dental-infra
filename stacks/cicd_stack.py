"""CiCdStack — GitHub OIDC provider and IAM roles for CI/CD pipelines.

Creates three least-privilege roles, one per repository:
  - Backend:  push to ECR + trigger ECS deployment
  - Frontend: sync to S3  + invalidate CloudFront
  - Infra:    full CDK deploy (CloudFormation, IAM, etc.)

All roles use OIDC federation with GitHub Actions — no static AWS
access keys are stored anywhere.
"""

from aws_cdk import Stack
from aws_cdk import aws_iam as iam
from constructs import Construct


class CiCdStack(Stack):
    """GitHub OIDC identity provider and per-repo deploy roles."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        github_org: str,
        backend_repo: str,
        frontend_repo: str,
        infra_repo: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── GitHub OIDC Provider ────────────────────────────────────
        oidc_provider = iam.OpenIdConnectProvider(
            self,
            "GitHubOidc",
            url="https://token.actions.githubusercontent.com",
            client_ids=["sts.amazonaws.com"],
            # GitHub's OIDC thumbprint (standard, documented by GitHub)
            thumbprints=["6938fd4d98bab03faadb97b34396831e3780aea1"],
        )

        # ── Helper: create a role trusted by a specific repo ────────
        def _repo_role(
            role_id: str,
            repo_name: str,
            description: str,
        ) -> iam.Role:
            return iam.Role(
                self,
                role_id,
                assumed_by=iam.FederatedPrincipal(
                    oidc_provider.open_id_connect_provider_arn,
                    conditions={
                        "StringEquals": {
                            "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                        },
                        "StringLike": {
                            "token.actions.githubusercontent.com:sub": (
                                f"repo:{github_org}/{repo_name}:ref:refs/heads/main"
                            ),
                        },
                    },
                    assume_role_action="sts:AssumeRoleWithWebIdentity",
                ),
                description=description,
                max_session_duration=None,  # default 1h
            )

        # ── Backend Role ────────────────────────────────────────────
        self.backend_role = _repo_role(
            "BackendDeployRole",
            backend_repo,
            "Allows GitHub Actions in the backend repo to push Docker images to ECR and update ECS",
        )
        self.backend_role.add_to_policy(
            iam.PolicyStatement(
                sid="ECRPush",
                actions=[
                    "ecr:GetAuthorizationToken",
                    "ecr:BatchCheckLayerAvailability",
                    "ecr:InitiateLayerUpload",
                    "ecr:UploadLayerPart",
                    "ecr:CompleteLayerUpload",
                    "ecr:PutImage",
                    "ecr:BatchGetImage",
                    "ecr:GetDownloadUrlForLayer",
                ],
                resources=["*"],  # scoped further via ECR resource policy
            )
        )
        self.backend_role.add_to_policy(
            iam.PolicyStatement(
                sid="ECSUpdate",
                actions=[
                    "ecs:UpdateService",
                    "ecs:DescribeServices",
                    "ecs:DescribeTaskDefinition",
                    "ecs:RegisterTaskDefinition",
                    "ecs:ListTasks",
                    "ecs:DescribeTasks",
                ],
                resources=["*"],
            )
        )
        self.backend_role.add_to_policy(
            iam.PolicyStatement(
                sid="PassTaskRoles",
                actions=["iam:PassRole"],
                resources=["*"],
                conditions={
                    "StringLike": {
                        "iam:PassedToService": "ecs-tasks.amazonaws.com",
                    },
                },
            )
        )

        # ── Frontend Role ───────────────────────────────────────────
        self.frontend_role = _repo_role(
            "FrontendDeployRole",
            frontend_repo,
            "Allows GitHub Actions in the frontend repo to sync S3 and invalidate CloudFront",
        )
        self.frontend_role.add_to_policy(
            iam.PolicyStatement(
                sid="S3Sync",
                actions=[
                    "s3:PutObject",
                    "s3:GetObject",
                    "s3:ListBucket",
                    "s3:DeleteObject",
                ],
                resources=["arn:aws:s3:::acme-dental-ui-*", "arn:aws:s3:::acme-dental-ui-*/*"],
            )
        )
        self.frontend_role.add_to_policy(
            iam.PolicyStatement(
                sid="CloudFrontInvalidation",
                actions=["cloudfront:CreateInvalidation"],
                resources=["*"],
            )
        )

        # ── Infra Role ──────────────────────────────────────────────
        self.infra_role = _repo_role(
            "InfraDeployRole",
            infra_repo,
            "Allows GitHub Actions in the infra repo to run CDK deploy",
        )
        # CDK deploy needs broad permissions — admin-like for the
        # services it manages.  In a real org this would be scoped
        # further via Permission Boundaries.
        self.infra_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AdministratorAccess")
        )
