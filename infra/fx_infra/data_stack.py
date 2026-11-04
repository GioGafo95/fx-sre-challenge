from aws_cdk import RemovalPolicy, Stack, CfnOutput, aws_dynamodb as ddb, aws_secretsmanager as sm
from constructs import Construct


class DataStack(Stack):
    """Stateful resources only. Kept separate so app changes can never touch the data."""

    def __init__(self, scope: Construct, cid: str, existing_secret_arn: str | None = None, **kw) -> None:
        super().__init__(scope, cid, **kw)

        # ~30 rows of data: on-demand billing costs cents and scales without capacity planning.
        self.table = ddb.Table(
            self, "Rates",
            partition_key=ddb.Attribute(name="pk", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="sk", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery_specification=ddb.PointInTimeRecoverySpecification(
                point_in_time_recovery_enabled=True),
            encryption=ddb.TableEncryption.AWS_MANAGED,
            deletion_protection=True,
            removal_policy=RemovalPolicy.RETAIN,
        )

        if existing_secret_arn:
            # Token already stored by the account owner: it never passes through CloudFormation.
            self.token_secret = sm.Secret.from_secret_complete_arn(self, "BanxicoToken", existing_secret_arn)
        else:
            # Created with a random placeholder. scripts/post-deploy.sh replaces it with the real token.
            self.token_secret = sm.Secret(
                self, "BanxicoToken",
                description="Banxico SIE API token (replace the placeholder after first deploy)",
                generate_secret_string=sm.SecretStringGenerator(exclude_punctuation=True, password_length=32),
                removal_policy=RemovalPolicy.RETAIN,
            )

        CfnOutput(self, "TableName", value=self.table.table_name)
        CfnOutput(self, "TokenSecretArn", value=self.token_secret.secret_arn)
