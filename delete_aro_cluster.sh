#!/bin/bash

set -o nounset
set -o errexit
set -o pipefail
set -o verbose

export CLUSTER=$CLUSTER_NAME
export RESOURCEGROUP=$ARO_RG
export ARO_VNET="aro-vnet"

# Delete the cluster
az aro delete --resource-group $RESOURCEGROUP --name $CLUSTER

# Delete virtual network created for the cluster subnets
az network vnet delete --name $ARO_VNET --resource-group $RESOURCEGROUP

