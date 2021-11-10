# yc-github-runner

Ephemeral Yandex Cloud GitHub self-hosted runner.

## Usage

```yaml
jobs:
  create-runner:
    runs-on: ubuntu-latest
    outputs:
      label: ${{ steps.create-runner.outputs.label }}
    steps:
      - id: create-runner
        uses: placeholder/yc-github-runner@v0.3
        with:
          token: ${{ secrets.GH_SA_TOKEN }}
          project_id: ${{ secrets.GCP_PROJECT_ID }}
          service_account_key: ${{ secrets.GCP_SA_KEY }}
          image_family: debian-10

  test:
    needs: create-runner
    runs-on: ${{ needs.create-runner.outputs.label }}
    steps:
      - run: echo "This runs on the GCE VM"
      - uses: placeholder/gce-github-runner@v0.3
        with:
          command: stop
        if: always()
```

 * `create-runner` creates a Yandex Cloud VM and registers the runner with a unique label.
 * `test` runs a command on the runner, and destroys it after.

## Inputs

| Name | Required | Default | Description |
| ---- | -------- | ------- | ----------- |
| `command` | True | `start` | `start` or `stop` of the runner. |
| `token` | True |  | GitHub auth token, needs `repo`/`public_repo` scope: https://docs.github.com/en/rest/reference/actions#self-hosted-runners. |
| `folder_id` | True |  | Yandex Cloud folder ID.
| `service_account_key` | True |  | The service account key for Yandex Cloud authentication. This key should be created and stored as a secret. Should be JSON key. |
| `runner_ver` | True | `2.278.0` | Version of the GitHub Runner. |
| `az` | True | `us-east1-c` | Availability zone. |
| `name_prefix` | False | `yc-gh-runner` | VM name prefix for easier identification. |
| `memory` | False |  | RAM, in GB. Default is 2. |
| `cores` | False |  | Amount of CPU cores. Default is 2. |
| `disk_size` | False |  | VM disk size. Default depends on image type. |
| `runner_service_account` | False |  | Service account of the VM, defaults to default compute service account. Should have the permission to delete VMs (self delete). |
| `image_project` | False |  | The Google Cloud project against which all image and image family references will be resolved. |
| `image` | False |  | ID of the image that the disk will be initialized with. Either `image` or `image_family` is required. |
| `image_family` | False |  | The image family for the operating system that the boot disk will be initialized with. Either `image` or `image_family` is required. |
| `shutdown_timeout` | True | `30` | Grace period for the `stop` command, in seconds. |
| `actions_preinstalled` | True | `false` | Whether the GitHub actions have already been installed at `/actions-runner`. |

The base image for the runner needs to have these packages pre-installed:
 * `gcloud`
 * `git`
 * `at`
 * (optionally) GitHub Actions Runner (see `actions_preinstalled` parameter)

## Example Workflows

* [Test Workflow](./.github/workflows/test.yml): Test workflow.
