# k6 Operator for Kubernetes

[![CharmHub Badge](https://charmhub.io/k6-k8s/badge.svg)](https://charmhub.io/k6-k8s)
[![Release](https://github.com/canonical/k6-k8s-operator/actions/workflows/release.yaml/badge.svg)](https://github.com/canonical/k6-k8s-operator/actions/workflows/release.yaml)
[![Discourse Status](https://img.shields.io/discourse/status?server=https%3A%2F%2Fdiscourse.charmhub.io&style=flat&label=CharmHub%20Discourse)](https://discourse.charmhub.io)

This repository contains the source code for a Charmed Operator that drives [k6](https://github.com/grafana/k6) on Kubernetes. 

## Usage

Assuming you have access to a bootstrapped Juju controller on Kubernetes, you can:

```bash
$ juju deploy k6-k8s # --trust (use when Kubernetes has RBAC enabled)
```

## OCI Images

This charm, by default, deploys `ubuntu/xk6:0-24.04`.

## Contributing

Please see the [Juju SDK docs](https://juju.is/docs/sdk) for guidelines on enhancements to this charm following best practice guidelines, and the [contributing](https://github.com/canonical/k6-k8s-operator/blob/main/CONTRIBUTING.md) doc for developer guidance.
