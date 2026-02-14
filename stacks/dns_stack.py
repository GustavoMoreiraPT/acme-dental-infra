"""DnsStack — Route 53 hosted zone and ACM certificate.

Deployed first.  After the initial deploy, the NS records from the
hosted zone must be manually added to Namecheap so that Route 53
owns DNS for the subdomain.
"""

from aws_cdk import CfnOutput, Stack
from aws_cdk import aws_certificatemanager as acm
from aws_cdk import aws_route53 as route53
from constructs import Construct


class DnsStack(Stack):
    """Create the Route 53 hosted zone and ACM certificate."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        domain_name: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ── Route 53 Hosted Zone ────────────────────────────────────
        self.hosted_zone = route53.PublicHostedZone(
            self,
            "HostedZone",
            zone_name=domain_name,
            comment="Acme Dental AI Agent — delegated from Namecheap",
        )

        # ── ACM Certificate (DNS-validated via Route 53) ────────────
        # Must be in us-east-1 for CloudFront — we use cross-region
        # reference if the stack is in another region.  For simplicity
        # we deploy this whole stack in us-east-1.
        self.certificate = acm.Certificate(
            self,
            "Certificate",
            domain_name=domain_name,
            validation=acm.CertificateValidation.from_dns(self.hosted_zone),
        )

        # ── Outputs ─────────────────────────────────────────────────
        CfnOutput(
            self,
            "HostedZoneId",
            value=self.hosted_zone.hosted_zone_id,
            description="Route 53 hosted zone ID",
        )
        CfnOutput(
            self,
            "NameServers",
            value=", ".join(self.hosted_zone.hosted_zone_name_servers or []),
            description="Add these NS records to Namecheap for subdomain delegation",
        )
        CfnOutput(
            self,
            "CertificateArn",
            value=self.certificate.certificate_arn,
            description="ACM certificate ARN",
        )
