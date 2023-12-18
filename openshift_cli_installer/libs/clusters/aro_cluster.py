


class AroCluster(OcmCluster):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.logger = get_logger(f"{self.__class__.__module__}-{self.__class__.__name__}")

        # TODO: set region, hw requirements, cluster name, pull-secret etc.

    #TODO: move to cloud-tools
    def get_aro_client(self):
        pass
    def create_resource_group(self):
        pass

    def create_virtual_network(self):
        pass

    def create_master_subnet(self):
        pass

    def create_worker_subnet(self):
        pass

    def create_cluster(self):
        pass

    def destroy_cluster(self):
        pass

