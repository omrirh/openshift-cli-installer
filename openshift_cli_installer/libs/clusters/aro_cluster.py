from clouds.azure.azure_utils import *
from clouds.azure.session_clients import *


class AroCluster(OcpCluster):
    # See Azure SDK generated samples for more info:
    # https://github.com/Azure/azure-sdk-for-python/tree/main/sdk
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.logger = get_logger(f"{self.__class__.__module__}-{self.__class__.__name__}")

        # Init azure clients
        azure_credentials = get_azure_credentials()
        self.aro_client = get_aro_client(credential=azure_credentials)
        self.network_client = get_network_client(credential=azure_credentials)
        self.resource_client = get_resource_client(credential=azure_credentials)

        # Get resource geo-location
        self.region = kwargs.get("region", "eastus")

        # Create resource group
        resource_group_name = f"aro-rg-{random_resource_postfix()}"
        print(f"Creating resource group {resource_group_name}")
        resource_client.resource_groups.create_or_update(
            resource_group_name=resource_group_name, parameters={"location": region}
        )

        # Create virtual network
        vnet_address_prefix = "10.0.0.0/16"
        virtual_network_name = f"aro-vnet-{random_resource_postfix()}"
        print(f"Creating virtual network {virtual_network_name}")
        network_client.virtual_networks.begin_create_or_update(
            virtual_network_name=virtual_network_name,
            resource_group_name=resource_group_name,
            parameters={"location": region, "address_space": {"address_prefixes": [vnet_address_prefix]}},
        ).result()

        # Create virtual network master+worker subnets
        workers_subnet_name = f"workers-subnet-{random_resource_postfix()}"
        master_subnet_name = f"master-subnet-{random_resource_postfix()}"

        print(f"Creating workers subnet {workers_subnet_name}")
        network_client.subnets.begin_create_or_update(
            resource_group_name=resource_group_name,
            virtual_network_name=virtual_network_name,
            subnet_name=workers_subnet_name,
            subnet_parameters={"properties": {"addressPrefix": vnet_address_prefix}},
        ).result()

        print(f"Creating workers subnet {master_subnet_name}")
        network_client.subnets.begin_create_or_update(
            resource_group_name=resource_group_name,
            virtual_network_name=virtual_network_name,
            subnet_name=workers_subnet_name,
            subnet_parameters={"properties": {"addressPrefix": vnet_address_prefix}},
        ).result()

        # create test aro cluster
        aro_cluster_name = f"msi-aro-{random_resource_postfix()}"
        print(f"Creating ARO cluster {aro_cluster_name}")
        aro_cluster = create_aro_cluster(
            aro_client=aro_client,
            resource_client=resource_client,
            region=region,
            domain=aro_cluster_name,
            resource_group_name=resource_group_name,
            virtual_network_name=virtual_network_name,
            cluster_name=aro_cluster_name,
            cluster_version="4.13.23",
            pull_secret=get_pull_secret(),
            master_subnet_name=master_subnet_name,
            worker_subnet_name=workers_subnet_name,
        )

        import ipdb

        ipdb.set_trace()

        # delete test aro_cluster
        delete_cluster_res = delete_aro_cluster(
            aro_client=aro_client, resource_group_name=resource_group_name, cluster_name=aro_cluster_name
        )

    def create_cluster(
        self,
        aro_client=None,
        resource_client=None,
        cluster_name=None,
        cluster_version=None,
        resource_group_name=None,
        pull_secret=None,
        region=None,
        domain=None,
        virtual_network_name=None,
        worker_subnet_name=None,
        master_subnet_name=None,
        master_vm_size="Standard_D8s_v3",
        worker_vm_size="Standard_D4s_v3",
        workers_count=3,
        workers_disk_size=128,
        fips=False,
        timeout=CLUSTER_TIMEOUT_MIN,
    ):
        """
        Args:
            aro_client (AzureRedHatOpenShiftClient): Instance to interact with ARO resources
            resource_client (ResourceManagementClient): Instance to interact with Azure resources
            cluster_name (str): Target cluster name
            cluster_version (str): Target cluster version
            pull_secret (str): Target cluster pull-secret content
            master_vm_size (str): Master nodes vm type (see supported types in the documentation)
            worker_vm_size (str): Worker nodes vm type (see supported types in the documentation)
            workers_count (int): number of worker nodes
            workers_disk_size (int): Worker nodes disk size
            fips (bool): FIPS configuration on target cluster
            region (str): Target cluster location
            timeout (int):
            domain (str): target cluster domain
            resource_group_name (str):
            virtual_network_name (str):
            worker_subnet_name (str):
            master_subnet_name (str):
        """
        subscription_id = get_subscription_id()

        # TODO: validate cluster params (supported region/version/worker + master vm size etc.)
        assert cluster_version in get_aro_supported_versions(aro_client=aro_client, region=region)

        # create cluster resource group
        cluster_resource_group_name = f"{cluster_name}-rg"
        print(f"Create cluster resource group {cluster_resource_group_name}")
        resource_client.resource_groups.create_or_update(
            resource_group_name=resource_group_name,
            parameters={"location": region},
        )

        # TODO: check how to handle stage/prod
        cluster_params = {
            "clusterProfile": {
                "domain": domain,
                "fipsValidatedModules": "Enabled" if fips else "Disabled",
                "pullSecret": pull_secret,
                "resourceGroupId": f"/subscriptions/{subscription_id}/resourcegroups/{cluster_resource_group_name}",
                "version": cluster_version,
            },
            "masterProfile": {
                "encryptionAtHost": "Enabled",
                "subnetId": f"/subscriptions/{subscription_id}/resourceGroups/{resource_group_name}/providers/Microsoft"
                f".Network/virtualNetworks/{virtual_network_name}/subnets/{master_subnet_name}",
                "vmSize": master_vm_size,
            },
            "workerProfiles": [
                {
                    "count": workers_count,
                    "diskSizeGB": workers_disk_size,
                    "name": "worker",
                    "subnetId": f"/subscriptions/{subscription_id}/resourceGroups/{resource_group_name}/providers/Microsoft"
                    f".Network/virtualNetworks/{virtual_network_name}/subnets/{worker_subnet_name}",
                    "vmSize": worker_vm_size,
                }
            ],
            "servicePrincipalProfile": {
                "clientId": os.environ[azure_client_credentials_env_vars["client_id"]],
                "clientSecret": os.environ[azure_client_credentials_env_vars["client_secret"]],
            },
            "apiserverProfile": {"visibility": "Public"},
        }

        # TODO: add option to config ingressProfiles/networkProfile/servicePrincipalProfile/tags ?
        aro_cluster = aro_client.open_shift_clusters.begin_create_or_update(
            resource_name=cluster_name,
            resource_group_name=resource_group_name,
            parameters={"location": region, "properties": cluster_params},
        )

        # TODO: add timeout watcher for cluster provisioning

        return aro_cluster

    def destroy_cluster(
        self,
        aro_client=None,
        cluster_name=None,
        network_client=None,
        resource_client=None,
        resource_group_name=None,
        virtual_network_name=None,
    ):
        # Delete ARO cluster
        print(f"Deleting ARO cluster {cluster_name}")
        aro_cluster_delete = aro_client.open_shift_clusters.begin_delete(
            resource_group_name=resource_group_name,
            resource_name=cluster_name,
        )

        # Delete virtual network
        print(f"Deleting virtual network {virtual_network_name}")
        network_client.virtual_networks.begin_delete(
            resource_group_name=resource_group_name,
            virtual_network_name=virtual_network_name,
        ).result()

        # Delete resource group
        print(f"Deleting resource group {resource_group_name}")
        resource_client.resource_groups.begin_delete(
            resource_group_name=resource_group_name,
        )

        return aro_cluster_delete

    def get_aro_supported_versions(self, aro_client=None, region=None):
        return [aro_version.version for aro_version in aro_client.open_shift_versions.list(location=region)]

    def random_resource_postfix(length=4):
        return "".join(random.choice(string.ascii_lowercase) for _ in range(length))

    def get_pull_secret(ps_path="pull-secret.txt"):
        with open(ps_path, "r") as ps_file:
            ps_data = ps_file.read()
            return ps_data


def main():
    import ipdb

    ipdb.set_trace()


if __name__ == "__main__":
    main()
