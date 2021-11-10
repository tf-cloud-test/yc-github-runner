#!/usr/bin/env python3

import json
import argparse
import logging
import grpc
import yandexcloud
import requests
import os
from jinja2 import Environment,FileSystemLoader,TemplateNotFound
import textwrap
import distutils.util

from yandex.cloud.compute.v1.image_service_pb2 import GetImageLatestByFamilyRequest
from yandex.cloud.compute.v1.image_service_pb2_grpc import ImageServiceStub
from yandex.cloud.compute.v1.instance_pb2 import IPV4, Instance
from yandex.cloud.compute.v1.instance_service_pb2 import (
    CreateInstanceRequest,
    ResourcesSpec,
    AttachedDiskSpec,
    NetworkInterfaceSpec,
    PrimaryAddressSpec,
    OneToOneNatSpec,
    DeleteInstanceRequest,
    CreateInstanceMetadata,
    DeleteInstanceMetadata,
)
from yandex.cloud.compute.v1.instance_service_pb2_grpc import InstanceServiceStub


def parse_args():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument('--action', help='`start` or `stop` the runner.')
    parser.add_argument('--github_auth_token',
        help='Github auth token.\nNeeds `repo`/`public_repo` scope: https://docs.github.com/en/rest/reference/actions#self-hosted-runners.')
    parser.add_argument(
        '--sa-json-path',
        help='Path to the service account key JSON file.'
    )
    parser.add_argument('--folder-id', help='Yandex.Cloud folder id', required=True)
    parser.add_argument(
        '--zone',
        help='Compute Engine zone to deploy to.\nTarget subnet must be in that zone.')
    parser.add_argument(
        '--subnet-id', help='Subnet of the instance.')
    parser.add_argument(
        '--name-prefix',
        help='VM name prefix.')
    parser.add_argument(
        '--runner-sa',
        help='Service account to bind to VM.')
    parser.add_argument(
        '--memory', help='Amount of RAM in GB.', type=int)
    parser.add_argument(
        '--cores', help='Amount of CPU cores.', type=int)
    parser.add_argument(
        '--disk-size', help='VM disk size.', type=int)
    parser.add_argument(
        '--image-family', help='Image family for init image for boot disk.')
    parser.add_argument(
        '--shutdown-timeout', help='Grace period for the `stop` command, in seconds.')
    parser.add_argument(
        '--actions-preinstalled', help='Whether the image has Runner pre-installed at `/actions-runner`.')
    parser.add_argument(
        '--runner-ver', help='Version of the GitHub Runner.')
    parser.add_argument(
        '--instance-id', help='YC VM instance id. Required by delete command.')
    return parser.parse_args()


def create_runner(
    sdk, github_auth_token, folder_id, zone, subnet_id, name_prefix,
    runner_sa_id, memory, cores, disk_size, image, runner_ver, actions_preinstalled
):
    run_id = os.getenv('GITHUB_RUN_ID')
    if (run_id == ''):
        raise Exception('Environment variable GITHUB_RUN_ID is undefined.')

    # Request Github registration token
    repository = os.getenv('GITHUB_REPOSITORY')
    if (repository == ''):
        raise Exception('Environment variable GITHUB_REPOSITORY is undefined.')

    url = 'https://api.github.com/repos/'+str(repository)+'/actions/runners/registration-token'
    logging.info('Requesting a token for repository %s' % repository)
    headers = {'Authorization': 'Bearer %s' % github_auth_token}
    r = requests.post(url, headers=headers)
    if not r.ok:
        logging.error('HTTP' + str(r.status_code))
        raise Exception('Failed to request github token.')
    else:
        logging.info('Successfully retrieved Github runner registration token.')
    runner_registration_token = r.json()['token']

    # Render cloud-init template for the VM
    action_path = os.getenv('GITHUB_ACTION_PATH')
    if (action_path == ''):
        raise Exception('Environment variable GITHUB_ACTION_PATH is undefined.')
    runner_name = name_prefix+'-'+run_id
    env = Environment(loader=FileSystemLoader(str(action_path)))
    if bool(distutils.util.strtobool(actions_preinstalled)):
        try: tmpl = env.get_template('cloud-config-without-actions.yaml.j2')
        except TemplateNotFound:
            raise Exception('Cloud-init config (cloud-config-without-actions.yaml.j2) is missing.')
    else:
        try: tmpl = env.get_template('cloud-config-with-actions.yaml.j2')
        except TemplateNotFound:
            raise Exception('Cloud-init config (cloud-config-with-actions.yaml.j2) is missing.')

    jinja_render = tmpl.render(
        repository = repository,
        registration_token = runner_registration_token,
        runner_name = runner_name,
        runner_ver = runner_ver
    )
    cloud_config = textwrap.wrap(jinja_render,width=65536,replace_whitespace=False)[0] # might be a hack

    # Create Yandex Cloud VM
    instance_service = sdk.client(InstanceServiceStub)
    operation = instance_service.Create(CreateInstanceRequest(
        folder_id = folder_id,
        labels = {'gh_ready':'0'},
        service_account_id = runner_sa_id,
        name = runner_name,
        resources_spec=ResourcesSpec(
            memory = memory * 2 ** 30,
            cores = cores,
            #TODO: core_fraction=0,
        ),
        zone_id = zone,
        platform_id = 'standard-v1',
        boot_disk_spec = AttachedDiskSpec(
            auto_delete = True,
            disk_spec = AttachedDiskSpec.DiskSpec(
                type_id = 'network-ssd',
                size = disk_size * 2 ** 30,
                image_id = image.id,
            )
        ),
        network_interface_specs=[
            NetworkInterfaceSpec(
                subnet_id = subnet_id,
                primary_v4_address_spec = PrimaryAddressSpec(
                    one_to_one_nat_spec = OneToOneNatSpec(
                        ip_version = IPV4,
                    )
                )
            ),
        ],
        metadata={
            'user-data': cloud_config,
        },
    ))
    logging.info('Creating initiated.')
    return operation


def main():
    logging.basicConfig(level=logging.INFO)
    arguments = parse_args()
    interceptor = yandexcloud.RetryInterceptor(max_retry_count=5, retriable_codes=[grpc.StatusCode.UNAVAILABLE])
    with open(arguments.sa_json_path) as infile:
        sdk = yandexcloud.SDK(interceptor=interceptor, service_account_key=json.load(infile))

    if arguments.action == 'start':
        image_service = sdk.client(ImageServiceStub)
        source_image = image_service.GetLatestByFamily(
            GetImageLatestByFamilyRequest(
                folder_id=arguments.folder_id,
                family=arguments.image_family
                #folder_id='standard-images',
                #family='ubuntu-2004-lts'
            )
        )
        instance_id = None
        operation = create_runner(
            sdk, arguments.github_auth_token, arguments.folder_id, arguments.zone, arguments.subnet_id,
            arguments.name_prefix, arguments.runner_sa, arguments.memory,
            arguments.cores, arguments.disk_size, source_image, arguments.runner_ver, arguments.actions_preinstalled
        )
        operation_result = sdk.wait_operation_and_get_result(
            operation,
            response_type=Instance,
            meta_type=CreateInstanceMetadata,
        )
        instance_id = operation_result.response.id
        run_id = os.getenv('GITHUB_RUN_ID')
        logging.info('instance_id: %s' % instance_id)
        print('::set-output name=label::'+arguments.name_prefix+'-'+str(run_id))
        print('::set-output name=instance_id::'+instance_id)

    if arguments.action == 'stop':
        repository = os.getenv('GITHUB_REPOSITORY')
        if (repository == ''):
            raise Exception('Environment variable GITHUB_REPOSITORY is undefined.')
        try: arguments.instance_id
        except NameError:
            raise Exception('instance_id is undefined.')

        logging.info('instance_id: %s' % arguments.instance_id)
        # Get runner name
        logging.info('Self-requesting runner name from instance metadata...')
        url = 'http://169.254.169.254/computeMetadata/v1/instance/name'
        headers = {'Metadata-Flavor': 'Google'}
        r = requests.get(url, headers=headers)
        if not r.ok:
            logging.error('HTTP' + str(r.status_code))
            raise Exception('Failed to request runner name.')
        else:
            runner_name = r.text
            logging.info('Runner name (=id) is %s', runner_name)

        # Unregister the runner
        logging.info('Requesting removal of the runner %s' % runner_name)
        url = 'https://api.github.com/repos/'+str(repository)+'/actions/runners/'+arguments.instance_id
        headers = {'Authorization': 'Bearer %s' % arguments.github_auth_token}
        r = requests.request('DELETE', url, headers=headers)
        if not r.ok:
            logging.error('HTTP' + str(r.status_code))
            logging.error('Failed to remove runner from Github; removing the VM.')
        else:
            logging.info('Successfully unregistered the runner %s' % runner_name)

        # Delete the instance
        logging.info('Deleting Runner VM.')
        instance_service = sdk.client(InstanceServiceStub)
        # FIXME: seems to not work on the same VM
        operation = instance_service.Delete(
            DeleteInstanceRequest(instance_id=arguments.instance_id))
        operation_result = sdk.wait_operation_and_get_result(
            operation,
            meta_type=DeleteInstanceMetadata,
            )


if __name__ == '__main__':
    main()
