#!/bin/bash



poetry run python openshift_cli_installer/cli.py \               
                                --action create \
                                --ocm-token=$OCM_TOKEN \
                                --cluster "name=disconnected-ipi;base_domain=air-gapped-ipi.aws.interop.ccitredhat.com;platform=aws;region=us-east-1;subnet-id=subnet-02a15eaee383bc8f0;quay-server=ec2-3-86-46-236.compute-1.amazonaws.com;route53-hosted-zone=Z05095251UEKFNUUGLB16;version=4.12.0;timeout=1h" \
                                --trusted-ca-file="/root/bastion.crt"
                                --aws-access-key-id=$AWS_ACCESS_KEY_ID \
                                --aws-secret-access-key=$AWS_SECRET_ACCESS_KEY \
                                --aws-account-id=$AWS_ACCOUNT_ID \
                                --clusters-install-data-directory="." \
                                --ssh-key-file="/root/.ssh/id_rsa.pub" \
                                --trusted-ca-file="/root/bastion.crt" \
                                --parallel

