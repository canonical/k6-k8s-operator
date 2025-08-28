output "app_name" {
  value = juju_application.k6.name
}

output "endpoints" {
  value = {
    # Provides
    provide_cmr_mesh = "provide-cmr-mesh",
    # Requires
    send_remote_write = "send-remote-write",
    logging           = "logging",
    receive-k6-tests  = "receive-k6-tests",
    service_mesh      = "service-mesh",
    require_cmr_mesh  = "require-cmr-mesh",
  }
}
