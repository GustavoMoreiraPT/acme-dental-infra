#!/usr/bin/env python3
"""CDK entry point for the Acme Dental infrastructure.

Deploy order:
  1. DnsStack   (get NS records, add them to Namecheap)
  2. CiCdStack  (GitHub OIDC + deploy roles)
  3. AppStack   (VPC, ECS, ALB, S3, CloudFront)
  4. MonitoringStack (Canary, alarms, dashboard)
"""

import aws_cdk as cdk

from stacks.app_stack import AppStack
from stacks.cicd_stack import CiCdStack
from stacks.dns_stack import DnsStack
from stacks.monitoring_stack import MonitoringStack

app = cdk.App()

# ── Context values from cdk.json ────────────────────────────────────
domain_name = app.node.try_get_context("domain_name")
github_org = app.node.try_get_context("github_org")
backend_repo = app.node.try_get_context("backend_repo")
frontend_repo = app.node.try_get_context("frontend_repo")
infra_repo = app.node.try_get_context("infra_repo")

# All stacks deployed to us-east-1 (required for CloudFront + ACM)
env = cdk.Environment(region="us-east-1")

# ── Stack 1: DNS + ACM Certificate ─────────────────────────────────
dns_stack = DnsStack(
    app,
    "AcmeDental-Dns",
    domain_name=domain_name,
    env=env,
)

# ── Stack 2: CI/CD (GitHub OIDC + IAM Roles) ───────────────────────
cicd_stack = CiCdStack(
    app,
    "AcmeDental-CiCd",
    github_org=github_org,
    backend_repo=backend_repo,
    frontend_repo=frontend_repo,
    infra_repo=infra_repo,
    env=env,
)

# ── Stack 3: Application (VPC, ECS, ALB, S3, CloudFront) ───────────
app_stack = AppStack(
    app,
    "AcmeDental-App",
    domain_name=domain_name,
    hosted_zone=dns_stack.hosted_zone,
    certificate=dns_stack.certificate,
    env=env,
)
app_stack.add_dependency(dns_stack)

# ── Stack 4: Monitoring (Canary, Alarms, Dashboard) ────────────────
monitoring_stack = MonitoringStack(
    app,
    "AcmeDental-Monitoring",
    domain_name=domain_name,
    distribution_id=app_stack.distribution.distribution_id,
    env=env,
)
monitoring_stack.add_dependency(app_stack)

app.synth()
