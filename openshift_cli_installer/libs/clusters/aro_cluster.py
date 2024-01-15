from clouds.azure.azure_utils import random_resource_postfix, get_aro_supported_versions
from clouds.azure.session_clients import (
    get_subscription_id,
    get_azure_credentials,
    get_aro_client,
    get_network_client,
    get_resource_client,
)
from openshift_cli_installer.utils.general import get_pull_secret


class AroCluster(OcpCluster):
    # See Azure SDK generated samples for more info:
    # https://github.com/Azure/azure-sdk-for-python/tree/main/sdk
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.logger = get_logger(f"{self.__class__.__module__}-{self.__class__.__name__}")

        # Init Azure clients
        azure_credentials = get_azure_credentials()
        self.subscription_id = get_subscription_id()
        self.aro_client = get_aro_client(credential=azure_credentials)
        self.network_client = get_network_client(credential=azure_credentials)
        self.resource_client = get_resource_client(credential=azure_credentials)

        # Set cluster parameters
        self.region = kwargs.get("region", "eastus")
        self.version = kwargs.get("version")
        self.cluster_name = kwargs.get("cluster-name", f"msi-aro-{random_resource_postfix()}")
        self.domain = kwargs.get("domain", self.cluster_name)
        self.resource_group_name = kwargs.get("resource-group-name", f"aro-rg-{random_resource_postfix()}")
        self.cluster_resource_group_name = kwargs.get("cluster-resource-group-name", f"{self.cluster_name}-rg")
        self.virtual_network_name = kwargs.get("virtual-network-name", f"aro-vnet-{random_resource_postfix()}")
        self.workers_subnet_name = kwargs.get("workers-subnet-name", f"workers-subnet-{random_resource_postfix()}")
        self.master_subnet_name = kwargs.get("master-subnet-name", f"master-subnet-{random_resource_postfix()}")
        self.master_vm_size = kwargs.get("master-vm-size", "Standard_D8s_v3"),
        self.worker_vm_size = kwargs.get("workers-vm-size", "Standard_D4s_v3"),
        self.workers_count = kwargs.get("workers-count", 3),
        self.workers_disk_size = kwargs.get("workers-disk-size", 128),
        self.network_pod_cidr = kwargs.get("network-pod-cidr", "10.128.0.0/14")
        self.network_service_cidr = kwargs.get("network-service-cidr", "172.30.0.0/16")
        self.fips = kwargs.get("fips", False),
        # self.timeout = kwargs.get("timeout", CLUSTER_TIMEOUT_MIN)

        if pull_secret_file := kwargs.get("pull-secret-file"):
            self.pull_secret = get_pull_secret(pull_secret_file=pull_secret_file)

        self.__assert_cluster_params_are_valid()
        self._prepare_cluster_resources()


    def __assert_cluster_params_are_valid(self):
        # TODO: Add more cluster params validation
        assert self.version in get_aro_supported_versions(aro_client=self.aro_client, region=self.region)

    def __prepare_cluster_resources(self):
        self.__create_resource_group(resource_group_name=self.resource_group_name)
        self.__create_resource_group(resource_group_name=self.cluster_resource_group_name)
        self.__create_virtual_network()

    def __destroy_cluster_resources(self):
        self.__delete_virtual_network()
        self.__delete_resource_group(resource_group_name=self.cluster_resource_group_name)
        self.__delete_resource_group(resource_group_name=self.resource_group_name)

    def __create_resource_group(self, resource_group_name=None):
        self.logger.info(f"Creating resource group {resource_group_name}")
        resource_group_create = self.resource_client.resource_groups.create_or_update(
            resource_group_name=resource_group_name, parameters={"location": self.region}
        ).result()

        return resource_group_create

    def __delete_resource_group(self, resource_group_name=None):
        self.logger.info(f"Deleting resource group {resource_group_name}")
        resource_group_delete = self.resource_client.resource_groups.begin_delete(
            resource_group_name=resource_group_name
        ).result()

        return resource_group_delete

    def __create_virtual_network(self, vnet_address_prefix="10.0.0.0/16"):
        self.logger.info(f"Creating virtual network {self.virtual_network_name}")
        vnet_create = self.network_client.virtual_networks.begin_create_or_update(
            virtual_network_name=self.virtual_network_name,
            resource_group_name=self.resource_group_name,
            parameters={"location": self.region, "address_space": {"address_prefixes": [vnet_address_prefix]}},
        ).result()

        self.logger.info(f"Creating master nodes subnet {self.master_subnet_name}")
        self.network_client.subnets.begin_create_or_update(
            resource_group_name=self.resource_group_name,
            virtual_network_name=self.virtual_network_name,
            subnet_name=self.master_subnet_name,
            subnet_parameters={"properties": {"addressPrefix": vnet_address_prefix}},
        )

        self.logger.info(f"Creating worker nodes subnet {self.workers_subnet_name}")
        self.network_client.subnets.begin_create_or_update(
            resource_group_name=self.resource_group_name,
            virtual_network_name=self.virtual_network_name,
            subnet_name=self.workers_subnet_name,
            subnet_parameters={"properties": {"addressPrefix": vnet_address_prefix}},
        )

        return vnet_create

    def __delete_virtual_network(self):
        self.logger.info(f"Deleting virtual network {self.virtual_network_name}")
        vnet_delete = self.network_client.virtual_networks.begin_delete(
            virtual_network_name=self.virtual_network_name,
            resource_group_name=self.resource_group_name,
        ).result()

        return vnet_delete

    def create_cluster(self):
        self.logger.info(f"Create cluster resource group {self.cluster_resource_group_name}")
        self.__create_resource_group(resource_group_name=self.cluster_resource_group_name)

        # TODO: check how to handle stage/prod
        cluster_params = {
            "clusterProfile": {
                "domain": self.domain,
                "fipsValidatedModules": "Enabled" if fips else "Disabled",
                "pullSecret": self.pull_secret,
                "resourceGroupId": f"/subscriptions/{self.subscription_id}/resourcegroups/{self.cluster_resource_group_name}",
                "version": cluster_version,
            },
            "masterProfile": {
                "encryptionAtHost": "Enabled",
                "subnetId": f"/subscriptions/{self.subscription_id}/resourceGroups/{self.resource_group_name}/providers/Microsoft"
                f".Network/virtualNetworks/{self.virtual_network_name}/subnets/{self.master_subnet_name}",
                "vmSize": self.master_vm_size,
            },
            "workerProfiles": [
                {
                    "count": self.workers_count,
                    "diskSizeGB": self.workers_disk_size,
                    "name": "worker",
                    "subnetId": f"/subscriptions/{self.subscription_id}/resourceGroups/{self.resource_group_name}/providers/Microsoft"
                    f".Network/virtualNetworks/{self.virtual_network_name}/subnets/{self.worker_subnet_name}",
                    "vmSize": self.worker_vm_size,
                }
            ],
            "servicePrincipalProfile": {
                "clientId": os.environ[azure_client_credentials_env_vars["client_id"]],
                "clientSecret": os.environ[azure_client_credentials_env_vars["client_secret"]],
            },
            "apiserverProfile": {"visibility": "Public"},
            "consoleProfile": {},
            "ingressProfiles": [{"name": "default", "visibility": "Public"}],
            "networkProfile": {
                "podCidr": self.network_pod_cidr,
                "preconfiguredNSG": "Disabled",
                "serviceCidr": self.network_service_cidr,
            },
        }

        self.logger.info(f"Creating ARO cluster {cluster_name}")
        aro_cluster_create = self.aro_client.open_shift_clusters.begin_create_or_update(
            resource_name=self.cluster_name,
            resource_group_name=self.resource_group_name,
            parameters={"location": self.region, "properties": cluster_params},
        ).result()

        # TODO: add timeout watcher for cluster provisioning

        return aro_cluster_create

    def destroy_cluster(self):
        self.logger.info(f"Destroying ARO cluster {cluster_name}")
        aro_cluster_delete = self.aro_client.open_shift_clusters.begin_delete(
            resource_group_name=self.cluster_resource_group_name,
            resource_name=self.cluster_name,
        ).result()

        # TODO: add timeout watcher for cluster delete

        self._destroy_cluster_resources()

        return aro_cluster_delete
