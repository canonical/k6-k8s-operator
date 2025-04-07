resource "juju_application" "k6" {
  name  = var.app_name
  model = var.model_name
  trust = true
  charm {
    name     = "k6-k8s"
    channel  = var.channel
    revision = var.revision
  }
  units  = var.units
  config = var.config
}
