import os
import re
import yaml
import base64
from clouds.azure.utils import get_aro_supported_versions
from clouds.azure.session_clients import (
    azure_credentials,
    aro_client,
    network_client,
    resource_client,
)
from openshift_cli_installer.libs.clusters.rosa_cluster import RosaCluster
from openshift_cli_installer.libs.clusters.ocp_cluster import OCPCluster
from simple_logger.logger import get_logger


class AROCluster(OCPCluster):
    def __init__(self, ocp_cluster, user_input):
        super().__init__(ocp_cluster=ocp_cluster, user_input=user_input)
        self.logger = get_logger(f"{self.__class__.__module__}-{self.__class__.__name__}")
        self.set_azure_authentication()

        if self.user_input.create:
            self.set_aro_cluster_params(cluster_data=self.cluster_info)
        else:
            with open(self.cluster_data_yaml_file, "r") as fd:
                cluster_data = yaml.safe_load(stream=fd)
                self.set_aro_cluster_params(cluster_data=cluster_data["cluster"])

    def set_azure_authentication(self):
        self.tenant_id = self.user_input.azure_tenant_id
        self.client_id = self.user_input.azure_client_id
        self.client_secret = self.user_input.azure_client_secret
        self.subscription_id = self.user_input.azure_subscription_id
        self.credentials = azure_credentials(
            tenant_id=self.tenant_id,
            client_id=self.client_id,
            client_secret=self.client_secret,
        )

        self.aro_client, self.network_client, self.resource_client = (
            client(credential=self.credentials, subscription_id=self.subscription_id)
            for client in (aro_client, network_client, resource_client)
        )

    def set_aro_cluster_params(self, cluster_data):
        self.cluster_name = self.cluster_info["name"]
        cluster_resources_postfix = RosaCluster.generate_hypershift_password()
        resource_group_name_str = "resource-group-name"
        virtual_network_name_str = "virtual-network-name"

        aro_cluster_args = {
            "domain": None,
            resource_group_name_str: f"aro-rg-{cluster_resources_postfix}",
            "cluster-resource-group-name": f"{self.cluster_name}-rg-{cluster_resources_postfix}",
            virtual_network_name_str: f"aro-vnet-{cluster_resources_postfix}",
            "workers-subnet-name": f"workers-subnet-{cluster_resources_postfix}",
            "master-subnet-name": f"master-subnet-{cluster_resources_postfix}",
            "master-vm-size": "Standard_D8s_v3",
            "workers-vm-size": "Standard_D4s_v3",
            "workers-count": 3,
            "workers-disk-size": 128,
            "network-pod-cidr": "10.128.0.0/14",
            "network-service-cidr": "172.30.0.0/16",
            "vnet-address-prefix": "10.0.0.0/22",
            "workers-subnet-address-prefix": "10.0.0.0/23",
            "master-subnet-address-prefix": "10.0.2.0/23",
            "fips": False,
        }
        self.cluster.update({
            aro_arg: cluster_data.pop(aro_arg, aro_val) for aro_arg, aro_val in aro_cluster_args.items()
        })

        if pull_secret_file := self.user_input.docker_config_file:
            with open(pull_secret_file, "r") as ps_file:
                self.pull_secret = ps_file.read()

        self.resource_group_name = self.cluster[resource_group_name_str]
        self.virtual_network_name = self.cluster[virtual_network_name_str]

        self.assert_cluster_params_are_valid()
        if not self.user_input.destroy_from_s3_bucket_or_local_directory:
            self.dump_aro_cluster_data_to_file()

    def dump_aro_cluster_data_to_file(self):
        self.logger.info(
            f"Writing {self.cluster['platform']} cluster {self.cluster_name} data to {self.cluster_data_yaml_file}"
        )
        with open(self.cluster_data_yaml_file, "r+") as fd:
            _cluster_data = yaml.safe_load(fd)
            _cluster_data["cluster"].update(self.cluster)
            fd.write(yaml.dump(_cluster_data))

    def assert_cluster_params_are_valid(self):
        def assert_is_supported_version():
            aro_supported_versions = get_aro_supported_versions(
                aro_client=self.aro_client,
                region=self.cluster["region"],
            )
            assert self.cluster["version"] in aro_supported_versions, (
                f"Version {self.cluster['version']} is not supported for {self.cluster['platform']}, supported "
                f"versions: {aro_supported_versions}"
            )

        def assert_is_valid_domain_name():
            assert re.match(pattern=r"^[a-zA-Z][a-zA-Z0-9.]{0,28}[a-zA-Z0-9]$", string=self.cluster["domain"]), (
                "Domain name must contain 1 to 30 alphanumeric characters or '.', and start and end with an "
                "alphabetic character"
            )

        assert_is_supported_version()
        assert_is_valid_domain_name()

    def create_cluster_resources(self):
        self.create_resource_group()
        self.create_virtual_network()

    def destroy_cluster_resources(self):
        self.delete_virtual_network()
        self.delete_resource_group()

    def create_resource_group(self):
        self.logger.info(f"Creating resource group {self.resource_group_name}")
        self.resource_client.resource_groups.create_or_update(
            resource_group_name=self.resource_group_name, parameters={"location": self.cluster["region"]}
        )

    def delete_resource_group(self):
        self.logger.info(f"Deleting resource group {self.resource_group_name}")
        self.resource_client.resource_groups.begin_delete(resource_group_name=self.resource_group_name)

    def create_virtual_network(self):
        def create_subnet(subnet_name, subnet_address_prefix):
            self.logger.info(f"Creating subnet {subnet_name} in virtual network {self.virtual_network_name}")
            self.network_client.subnets.begin_create_or_update(
                resource_group_name=self.resource_group_name,
                virtual_network_name=self.virtual_network_name,
                subnet_name=subnet_name,
                subnet_parameters={"properties": {"addressPrefix": subnet_address_prefix}},
            ).result()

        self.logger.info(f"Creating virtual network {self.virtual_network_name}")
        self.network_client.virtual_networks.begin_create_or_update(
            virtual_network_name=self.virtual_network_name,
            resource_group_name=self.resource_group_name,
            parameters={
                "location": self.cluster["region"],
                "address_space": {"address_prefixes": [self.cluster["vnet-address-prefix"]]},
            },
        ).result()

        create_subnet(
            subnet_name=self.cluster["master-subnet-name"],
            subnet_address_prefix=self.cluster["master-subnet-address-prefix"],
        )
        create_subnet(
            subnet_name=self.cluster["workers-subnet-name"],
            subnet_address_prefix=self.cluster["workers-subnet-address-prefix"],
        )

    def delete_virtual_network(self):
        self.logger.info(f"Deleting virtual network {self.virtual_network_name}")
        self.network_client.virtual_networks.begin_delete(
            virtual_network_name=self.virtual_network_name,
            resource_group_name=self.resource_group_name,
        ).result()

    def aro_cluster_kubeconfig(self):
        self.logger.info(f"Fetching {self.cluster['platform']} cluster {self.cluster_name} kubeconfig")
        return (
            base64.b64decode(
                s=self.aro_client.open_shift_clusters.list_admin_credentials(
                    resource_group_name=self.resource_group_name, resource_name=self.cluster_name
                ).kubeconfig
            )
        ).decode("utf-8")

    def aro_cluster_kubeadmin_password(self):
        self.logger.info(f"Fetching {self.cluster['platform']} cluster {self.cluster_name} kubeadmin-password")
        return self.aro_client.open_shift_clusters.list_credentials(
            resource_group_name=self.resource_group_name,
            resource_name=self.cluster_name,
        ).kubeadmin_password

    def set_aro_cluster_auth(self):
        auth_path = self.cluster_info["auth-path"]
        with open(os.path.join(auth_path, "kubeconfig"), "w") as kubeconfig:
            kubeconfig.write(self.aro_cluster_kubeconfig())

        with open(os.path.join(auth_path, "kubeadmin-password"), "w") as kubeadmin:
            kubeadmin.write(self.aro_cluster_kubeadmin_password())

    def create_cluster(self):
        self.create_cluster_resources()

        cluster_body = {
            "clusterProfile": {
                "domain": self.cluster["domain"],
                "fipsValidatedModules": "Enabled" if self.cluster["fips"] else "Disabled",
                "pullSecret": self.pull_secret,
                "resourceGroupId": f"/subscriptions/{self.subscription_id}/resourcegroups/{self.cluster['cluster-resource-group-name']}",
                "version": self.cluster["version"],
            },
            "masterProfile": {
                "encryptionAtHost": "Enabled",
                "subnetId": f"/subscriptions/{self.subscription_id}/resourceGroups/{self.resource_group_name}/providers/Microsoft"
                f".Network/virtualNetworks/{self.virtual_network_name}/subnets/{self.cluster['master-subnet-name']}",
                "vmSize": self.cluster["master-vm-size"],
            },
            "workerProfiles": [
                {
                    "count": self.cluster["workers-count"],
                    "diskSizeGB": self.cluster["workers-disk-size"],
                    "name": "worker",
                    "encryptionAtHost": "Enabled",
                    "subnetId": f"/subscriptions/{self.subscription_id}/resourceGroups/{self.resource_group_name}/providers/Microsoft"
                    f".Network/virtualNetworks/{self.virtual_network_name}/subnets/{self.cluster['workers-subnet-name']}",
                    "vmSize": self.cluster["workers-vm-size"],
                }
            ],
            "servicePrincipalProfile": {
                "clientId": self.client_id,
                "clientSecret": self.client_secret,
            },
            "apiserverProfile": {"visibility": "Public"},
            "consoleProfile": {},
            "ingressProfiles": [{"name": "default", "visibility": "Public"}],
            "networkProfile": {
                "podCidr": self.cluster["network-pod-cidr"],
                "preconfiguredNSG": "Disabled",
                "serviceCidr": self.cluster["network-service-cidr"],
            },
        }

        self.logger.info(f"Creating {self.cluster['platform']} cluster {self.cluster_name}")

        try:
            self.aro_client.open_shift_clusters.begin_create_or_update(
                resource_name=self.cluster_name,
                resource_group_name=self.resource_group_name,
                parameters={"location": self.cluster["region"], "properties": cluster_body},
            ).result()
        except Exception as ex:
            self.logger.info(f"Failed to create {self.cluster['platform']} cluster {self.cluster_name}: {ex}")
            self.destroy_cluster()
            raise

        self.set_aro_cluster_auth()

        self.logger.info(f"{self.cluster['platform']} cluster {self.cluster_name} created successfully.")

    def destroy_cluster(self):
        self.logger.info(f"Destroying {self.cluster['platform']} cluster {self.cluster_name}")

        try:
            self.aro_client.open_shift_clusters.begin_delete(
                resource_group_name=self.resource_group_name,
                resource_name=self.cluster_name,
            ).result()
            self.destroy_cluster_resources()
        except Exception as ex:
            self.logger.info(f"Failed to delete {self.cluster['platform']} cluster {self.cluster_name}: {ex}")
            raise

        self.logger.info(f"{self.cluster['platform']} cluster {self.cluster_name} destroyed successfully.")
