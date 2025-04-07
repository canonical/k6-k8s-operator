output "app_name" {
  value = juju_application.k6.name
}

output "endpoints" {
  value = {
    # Requires
    send_remote_write = "send-remote-write",
    logging           = "logging",
    receive-k6-tests  = "receive-k6-tests",
  }
}
