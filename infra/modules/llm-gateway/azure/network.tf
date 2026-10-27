# Candidate non-production topology; no deployment or network proof implied.
# Developer classic VNET injection routes to an internal ACA environment.
resource "azurerm_virtual_network" "main" {
  name                = "vnet-${local.name}"
  location            = var.location
  resource_group_name = azurerm_resource_group.main.name
  address_space       = ["10.42.0.0/16"]
  tags                = local.common
}

resource "azurerm_subnet" "apim" {
  name                 = "apim"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.42.0.0/24"]
  service_endpoints    = ["Microsoft.Storage"]
}

resource "azurerm_subnet" "apps" {
  name                 = "apps"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.42.2.0/23"]
  delegation {
    name = "container-apps"
    service_delegation {
      name    = "Microsoft.App/environments"
      actions = ["Microsoft.Network/virtualNetworks/subnets/join/action"]
    }
  }
}

resource "azurerm_network_security_group" "apim" {
  name                = "nsg-${local.name}-apim"
  location            = var.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.common
  security_rule {
    name                       = "FrontDoorOnly"
    priority                   = 100
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "443"
    source_address_prefix      = "AzureFrontDoor.Backend"
    destination_address_prefix = "VirtualNetwork"
  }
  security_rule {
    name                       = "Management"
    priority                   = 110
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "3443"
    source_address_prefix      = "ApiManagement"
    destination_address_prefix = "VirtualNetwork"
  }
  security_rule {
    name                       = "LoadBalancer"
    priority                   = 120
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "6390"
    source_address_prefix      = "AzureLoadBalancer"
    destination_address_prefix = "VirtualNetwork"
  }
  security_rule {
    name                       = "DenyDirectGateway"
    priority                   = 200
    direction                  = "Inbound"
    access                     = "Deny"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_ranges    = ["80", "443"]
    source_address_prefix      = "*"
    destination_address_prefix = "VirtualNetwork"
  }
}

resource "azurerm_subnet_network_security_group_association" "apim" {
  subnet_id                 = azurerm_subnet.apim.id
  network_security_group_id = azurerm_network_security_group.apim.id
}

resource "azurerm_network_security_group" "apps" {
  name                = "nsg-${local.name}-apps"
  location            = var.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.common
}
resource "azurerm_subnet_network_security_group_association" "apps" {
  subnet_id                 = azurerm_subnet.apps.id
  network_security_group_id = azurerm_network_security_group.apps.id
}

resource "azurerm_subnet" "private_endpoints" {
  name                 = "private-endpoints"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.main.name
  address_prefixes     = ["10.42.4.0/24"]
}

resource "azurerm_network_security_group" "private_endpoints" {
  name                = "nsg-${local.name}-private"
  location            = var.location
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.common
}
resource "azurerm_subnet_network_security_group_association" "private_endpoints" {
  subnet_id                 = azurerm_subnet.private_endpoints.id
  network_security_group_id = azurerm_network_security_group.private_endpoints.id
}

resource "azurerm_private_dns_zone" "data" {
  for_each            = { table = "privatelink.table.core.windows.net", vault = "privatelink.vaultcore.azure.net" }
  name                = each.value
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.common
}
resource "azurerm_private_dns_zone_virtual_network_link" "data" {
  # Keep instance keys independent of apply-time resource attributes so a
  # cold plan can determine both links before the DNS zones are created.
  for_each              = toset(["table", "vault"])
  name                  = "data"
  resource_group_name   = azurerm_resource_group.main.name
  private_dns_zone_name = azurerm_private_dns_zone.data[each.key].name
  virtual_network_id    = azurerm_virtual_network.main.id
  tags                  = local.common
}
resource "azurerm_private_endpoint" "data" {
  for_each            = { table = azurerm_storage_account.ledger.id, vault = azurerm_key_vault.main.id }
  name                = "pe-${local.name}-${each.key}"
  location            = var.location
  resource_group_name = azurerm_resource_group.main.name
  subnet_id           = azurerm_subnet.private_endpoints.id
  tags                = local.common
  private_service_connection {
    name                           = "data"
    private_connection_resource_id = each.value
    subresource_names              = [each.key]
    is_manual_connection           = false
  }
  private_dns_zone_group {
    name                 = "data"
    private_dns_zone_ids = [azurerm_private_dns_zone.data[each.key].id]
  }
}

resource "azurerm_private_dns_zone" "apps" {
  name                = azurerm_container_app_environment.main.default_domain
  resource_group_name = azurerm_resource_group.main.name
  tags                = local.common
}
resource "azurerm_private_dns_zone_virtual_network_link" "apps" {
  name                  = "apps"
  resource_group_name   = azurerm_resource_group.main.name
  private_dns_zone_name = azurerm_private_dns_zone.apps.name
  virtual_network_id    = azurerm_virtual_network.main.id
  tags                  = local.common
}
resource "azurerm_private_dns_a_record" "apps" {
  name                = "*"
  zone_name           = azurerm_private_dns_zone.apps.name
  resource_group_name = azurerm_resource_group.main.name
  ttl                 = 300
  records             = [azurerm_container_app_environment.main.static_ip_address]
  tags                = local.common
}
