# infra/terraform/00-crds/providers.tf
#
# Provider CONFIGURATION (not just the version constraint in versions.tf,
# D-002/D-03). Reads config_path from a variable, never a literal path, per
# Constitution III and D-000/D-10 ("terraform runs on the Linux node ... the
# repo stays parameterized").

variable "kubeconfig_path" {
  description = "Path to the kubeconfig Terraform's helm/kubernetes providers use. Host-specific (D-000/D-10) — no default, supplied via common.tfvars."
  type        = string
}

provider "kubernetes" {
  config_path = var.kubeconfig_path
}

provider "helm" {
  kubernetes = {
    config_path = var.kubeconfig_path
  }
}
