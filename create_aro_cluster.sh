#!/bin/bash

set -o nounset
set -o errexit
set -o pipefail
set -o verbose

export LOCATION=$REGION       # the location of your cluster
export RESOURCEGROUP=$ARO_RG  # the name of the resource group where you want to create your cluster
export CLUSTER=$CLUSTER_NAME  # the name of your cluster

# Create a resource group.
# ------------------------
# An Azure resource group is a logical group
# in which Azure resources are deployed and managed.
# When you create a resource group, you're asked to specify location.
# This location is where resource group metadata is stored,
# and it is also where your resources run in Azure if you
# don't specify another region during resource creation.
# Create a resource group using the az group create command.
az group create \
  --name $RESOURCEGROUP \
  --location $LOCATION

# Create a virtual network.
# -------------------------
# Azure Red Hat OpenShift clusters running OpenShift 4
# require a virtual network with two empty subnets,
# for the master and worker nodes.
# You can either create a new virtual network for this,
# or use an existing virtual network.
# Create a new virtual network in the same resource group
# you created earlier.
az network vnet create \
   --resource-group $RESOURCEGROUP \
   --name aro-vnet \
   --address-prefixes 10.0.0.0/22

# Add an empty subnet for the master nodes.
az network vnet subnet create \
  --resource-group $RESOURCEGROUP \
  --vnet-name aro-vnet \
  --name master-subnet \
  --address-prefixes 10.0.0.0/23

# Add an empty subnet for the worker nodes.
az network vnet subnet create \
  --resource-group $RESOURCEGROUP \
  --vnet-name aro-vnet \
  --name worker-subnet \
  --address-prefixes 10.0.2.0/23

# Create the cluster
az aro create \
  --resource-group $RESOURCEGROUP \
  --name $CLUSTER \
  --vnet aro-vnet \
  --master-subnet master-subnet \
  --worker-subnet worker-subnet \
  --pull-secret @pull-secret.txt
