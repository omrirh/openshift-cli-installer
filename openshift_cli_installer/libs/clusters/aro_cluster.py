import os
import yaml
from clouds.azure.azure_utils import get_aro_supported_versions
from clouds.azure.session_clients import (
    get_azure_credentials,
    get_aro_client,
    get_network_client,
    get_resource_client,
    get_authorization_client,
    get_subscription_id,
)
from clouds.azure.vars import azure_client_credentials_env_vars
from openshift_cli_installer.utils.general import random_resource_postfix
from openshift_cli_installer.libs.clusters.ocp_cluster import OCPCluster
from simple_logger.logger import get_logger


class AROCluster(OCPCluster):
    def __init__(self, ocp_cluster, user_input):
        super().__init__(ocp_cluster=ocp_cluster, user_input=user_input)
        self.logger = get_logger(f"{self.__class__.__module__}-{self.__class__.__name__}")
        self.set_azure_authentication()
        self.set_aro_cluster_params()

    def set_azure_authentication(self):
        azure_credentials = get_azure_credentials()
        self.subscription_id = get_subscription_id()
        env_vars = azure_client_credentials_env_vars
        self.tenant_id, self.client_id, self.client_secret = (
            os.environ[env_vars[key]] for key in ("tenant_id", "client_id", "client_secret")
        )

        clients = get_aro_client, get_network_client, get_resource_client, get_authorization_client
        self.aro_client, self.network_client, self.resource_client, self.authorization_client = (
            client(credential=azure_credentials) for client in clients
        )

    def set_aro_cluster_params(self):
        self.cluster_name = self.get_cluster_name()
        cluster_resources_postfix = random_resource_postfix()

        aro_args = {
            "domain": "msi",
            "resource-group-name": f"aro-rg-{cluster_resources_postfix}",
            "cluster-resource-group-name": f"{self.cluster_name}-rg-{cluster_resources_postfix}",
            "virtual-network-name": f"aro-vnet-{cluster_resources_postfix}",
            "workers-subnet-name": f"workers-subnet-{cluster_resources_postfix}",
            "master-subnet-name": f"master-subnet-{cluster_resources_postfix}",
            "master-vm-size": "Standard_D8s_v3",
            "workers-vm-size": "Standard_D4s_v3",
            "workers-count": 3,
            "workers-disk-size": 128,
            "network-pod-cidr": "10.128.0.0/14",
            "network-service-cidr": "172.30.0.0/16",
            "fips": False,
        }
        self.cluster.update({k: self.cluster_info.pop(k, v) for k, v in aro_args.items()})

        if pull_secret_file := self.cluster_info.get("pull-secret-file"):
            with open(pull_secret_file, "r") as ps_file:
                self.cluster["pull-secret"] = ps_file.read()

        self.resource_group_name, self.virtual_network_name = (
            self.cluster["resource-group-name"],
            self.cluster["virtual-network-name"],
        )
        self.assert_cluster_params_are_valid()
        if not self.user_input.destroy_from_s3_bucket_or_local_directory:
            self.dump_aro_cluster_data_to_file()

    def dump_aro_cluster_data_to_file(self):
        self.logger.info(f"Writing ARO cluster {self.cluster_name} data to {self._cluster_data_yaml_file}")
        with open(self._cluster_data_yaml_file, "r+") as fd:
            _cluster_data = yaml.safe_load(fd)
            _cluster_data["cluster"].update(self.cluster)
            fd.write(yaml.dump(_cluster_data))

    def assert_cluster_params_are_valid(self):
        def assert_is_supported_version():
            cluster_version = self.cluster["version"]
            aro_supported_versions = get_aro_supported_versions(
                aro_client=self.aro_client, region=self.cluster["region"]
            )
            assert cluster_version in aro_supported_versions, (
                f"Version {cluster_version} is not supported for ARO, supported " f"versions: {aro_supported_versions}"
            )

        assert_is_supported_version()

    def create_cluster_resources(self):
        self.create_resource_group(resource_group_name=self.resource_group_name)
        self.create_virtual_network()

    def destroy_cluster_resources(self):
        self.delete_virtual_network()
        self.delete_resource_group(resource_group_name=self.resource_group_name)

    def create_resource_group(self, resource_group_name=None):
        self.logger.info(f"Creating resource group {resource_group_name}")
        resource_group_create = self.resource_client.resource_groups.create_or_update(
            resource_group_name=resource_group_name, parameters={"location": self.cluster["region"]}
        )

        return resource_group_create

    def delete_resource_group(self, resource_group_name=None):
        self.logger.info(f"Deleting resource group {resource_group_name}")
        resource_group_delete = self.resource_client.resource_groups.begin_delete(
            resource_group_name=resource_group_name
        )

        return resource_group_delete

    def create_virtual_network(
        self,
        vnet_address_prefix="10.0.0.0/22",
        workers_subnet_address_prefix="10.0.0.0/23",
        master_subnet_address_prefix="10.0.2.0/23",
    ):
        def create_subnet(subnet_name, subnet_address_prefix):
            self.logger.info(f"Creating subnet {subnet_name} in virtual network {self.virtual_network_name}")
            self.network_client.subnets.begin_create_or_update(
                resource_group_name=self.resource_group_name,
                virtual_network_name=self.virtual_network_name,
                subnet_name=subnet_name,
                subnet_parameters={"properties": {"addressPrefix": subnet_address_prefix}},
            ).result()

        self.logger.info(f"Creating virtual network {self.virtual_network_name}")
        vnet_create = self.network_client.virtual_networks.begin_create_or_update(
            virtual_network_name=self.virtual_network_name,
            resource_group_name=self.resource_group_name,
            parameters={
                "location": self.cluster["region"],
                "address_space": {"address_prefixes": [vnet_address_prefix]},
            },
        ).result()

        # Create worker + master nodes subnets
        create_subnet(
            subnet_name=self.cluster["master-subnet-name"], subnet_address_prefix=master_subnet_address_prefix
        )
        create_subnet(
            subnet_name=self.cluster["workers-subnet-name"], subnet_address_prefix=workers_subnet_address_prefix
        )

        return vnet_create

    def delete_virtual_network(self):
        self.logger.info(f"Deleting virtual network {self.virtual_network_name}")
        vnet_delete = self.network_client.virtual_networks.begin_delete(
            virtual_network_name=self.virtual_network_name,
            resource_group_name=self.resource_group_name,
        ).result()

        return vnet_delete

    def create_cluster(self):
        self.create_cluster_resources()
        self.timeout_watch = self.start_time_watcher()

        cluster_body = {
            "clusterProfile": {
                "domain": self.cluster["domain"],
                "fipsValidatedModules": "Enabled" if self.cluster["fips"] else "Disabled",
                "pullSecret": self.cluster.get("pull-secret"),
                "resourceGroupId": f"/subscriptions/{self.subscription_id}/resourcegroups/{self.cluster["cluster-resource-group-name"]}",
                "version": self.cluster["version"],
            },
            "masterProfile": {
                "encryptionAtHost": "Enabled",
                "subnetId": f"/subscriptions/{self.subscription_id}/resourceGroups/{self.resource_group_name}/providers/Microsoft"
                f".Network/virtualNetworks/{self.virtual_network_name}/subnets/{self.cluster["master-subnet-name"]}",
                "vmSize": self.cluster["master-vm-size"],
            },
            "workerProfiles": [
                {
                    "count": self.cluster["workers-count"],
                    "diskSizeGB": self.cluster["workers-disk-size"],
                    "name": "worker",
                    "encryptionAtHost": "Enabled",
                    "subnetId": f"/subscriptions/{self.subscription_id}/resourceGroups/{self.resource_group_name}/providers/Microsoft"
                    f".Network/virtualNetworks/{self.virtual_network_name}/subnets/{self.cluster["workers-subnet-name"]}",
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

        self.logger.info(f"Creating ARO cluster {self.cluster_name}")
        aro_cluster_create = self.aro_client.open_shift_clusters.begin_create_or_update(
            resource_name=self.cluster_name,
            resource_group_name=self.resource_group_name,
            parameters={"location": self.cluster["region"], "properties": cluster_body},
        ).result()

        return aro_cluster_create

    def destroy_cluster(self):
        self.timeout_watch = self.start_time_watcher()
        self.logger.info(f"Destroying ARO cluster {self.cluster_name}")
        aro_cluster_delete = self.aro_client.open_shift_clusters.begin_delete(
            resource_group_name=self.cluster["cluster-resource-group-name"],
            resource_name=self.cluster_name,
        ).result()

        self.destroy_cluster_resources()

        return aro_cluster_delete
