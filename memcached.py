#!/usr/bin/env python3
# pylint: disable=missing-super-argument
import global_env
import group
import consul
from sense import Sense
import ip_pool
import random
import logging
import docker
import uuid
import time
import tarantool
import allocate
import datetime
import json
import task

class MemcachedTask(task.Task):
    memcached_task_type = None
    def __init__(self, group_id):
        super().__init__(self.memcached_task_type)
        self.group_id = group_id

    def get_dict(self, index=None):
        obj = super().get_dict(index)
        obj['group_id'] = self.group_id
        return obj

class CreateTask(MemcachedTask):
    memcached_task_type = "create_memcached"

class UpdateTask(MemcachedTask):
    memcached_task_type = "update_memcached"

class DeleteTask(MemcachedTask):
    memcached_task_type = "delete_memcached"

class Memcached(group.Group):
    def __init__(self, consul_host, group_id):
        super(Memcached, self).__init__(consul_host, group_id)

    @classmethod
    def get(cls, group_id):
        memc = Memcached(global_env.consul_host, group_id)

        return memc

    @classmethod
    def create(cls, create_task, name, memsize, check_period):
        group_id = create_task.group_id

        try:
            consul_obj = consul.Consul(host=global_env.consul_host,
                                       token=global_env.consul_acl_token)
            kv = consul_obj.kv

            create_task.log("Creating group '%s'", group_id)

            ip1 = ip_pool.allocate_ip()
            ip2 = ip_pool.allocate_ip()
            creation_time = datetime.datetime.now(datetime.timezone.utc).isoformat()

            kv.put('tarantool/%s/blueprint/type' % group_id, 'memcached')
            kv.put('tarantool/%s/blueprint/name' % group_id, name)
            kv.put('tarantool/%s/blueprint/memsize' % group_id, str(memsize))
            kv.put('tarantool/%s/blueprint/check_period' % group_id, str(check_period))
            kv.put('tarantool/%s/blueprint/creation_time' % group_id, creation_time)
            kv.put('tarantool/%s/blueprint/instances/1/addr' % group_id, ip1)
            kv.put('tarantool/%s/blueprint/instances/2/addr' % group_id, ip2)

            Sense.update()

            memc = Memcached(global_env.consul_host, group_id)

            create_task.log("Allocating instance to physical nodes")

            memc.allocate()
            Sense.update()

            create_task.log("Registering services")
            memc.register()
            Sense.update()

            create_task.log("Creating containers")
            memc.create_containers()
            Sense.update()

            create_task.log("Enabled replication")
            memc.enable_replication()

            create_task.log("Completed creating group")


            create_task.set_status(task.STATUS_SUCCESS)
        except Exception as ex:
            logging.exception("Failed to create group '%s'", group_id)
            create_task.set_status(task.STATUS_CRITICAL, str(ex))

            raise

        return memc

    def delete(self, delete_task):
        try:
            group_id = self.group_id

            delete_task.log("Unallocating instance")
            self.unallocate()

            delete_task.log("Unregistering services")
            self.unregister()

            delete_task.log("Removing containers")
            self.remove_containers()

            delete_task.log("Removing blueprint")
            self.remove_blueprint()

            delete_task.log("Completed removing group")

            Sense.update()
            delete_task.set_status(task.STATUS_SUCCESS)
        except Exception as ex:
            logging.exception("Failed to delete group '%s'", group_id)
            delete_task.set_status(task.STATUS_CRITICAL, str(ex))

            raise

    def upgrade(self, upgrade_task):
        try:
            group_id = self.group_id

            upgrade_task.log("Upgrading container 1")
            self.upgrade_container("1")

            upgrade_task.log("Upgrading container 2")
            self.upgrade_container("2")

            upgrade_task.log("Completed upgrading containers")

            Sense.update()
            upgrade_task.set_status(task.STATUS_SUCCESS)
        except Exception as ex:
            logging.exception("Failed to upgrade group '%s'", group_id)
            upgrade_task.set_status(task.STATUS_CRITICAL, str(ex))

            raise


    def update(self, name, memsize, docker_image_name, update_task):
        try:
            if name and name != self.blueprint['name']:
                self.rename(name, update_task)

            if memsize and memsize != self.blueprint['memsize']:
                self.resize(memsize, update_task)

            if docker_image_name:
                self.upgrade(update_task)

            update_task.set_status(task.STATUS_SUCCESS)
        except Exception as ex:
            logging.exception("Failed to update group '%s'", self.group_id)
            update_task.set_status(task.STATUS_CRITICAL, str(ex))

            raise

    def rename(self, name, update_task):
        consul_obj = consul.Consul(host=global_env.consul_host,
                                   token=global_env.consul_acl_token)
        kv = consul_obj.kv

        msg = "Renaming group '%s' to '%s'" % (self.group_id, name)
        update_task.log(msg)
        logging.info(msg)

        kv.put('tarantool/%s/blueprint/name' % self.group_id, name)


    def resize(self, memsize, update_task):
        consul_obj = consul.Consul(host=global_env.consul_host,
                                   token=global_env.consul_acl_token)
        kv = consul_obj.kv

        update_task.log("Resizing instance 1")
        self.resize_instance("1", memsize)
        update_task.log("Resizing instance 2")
        self.resize_instance("2", memsize)

        kv.put('tarantool/%s/blueprint/memsize' % self.group_id, str(memsize))
        update_task.log("Completed resizing")

    def allocate(self):
        consul_obj = consul.Consul(host=global_env.consul_host,
                                   token=global_env.consul_acl_token)
        kv = consul_obj.kv

        blueprint = self.blueprint

        host1 = allocate.allocate(blueprint['memsize'])
        host2 = allocate.allocate(blueprint['memsize'], anti_affinity=[host1])

        kv.put('tarantool/%s/allocation/instances/1/host' %
               self.group_id, host1)
        kv.put('tarantool/%s/allocation/instances/2/host' %
               self.group_id, host2)

    def unallocate(self):
        consul_obj = consul.Consul(host=global_env.consul_host,
                                   token=global_env.consul_acl_token)
        kv = consul_obj.kv

        logging.info("Unallocating '%s'", self.group_id)

        kv.delete("tarantool/%s/allocation" % self.group_id,
                  recurse=True)

    def register(self):
        self.register_instance("1")
        self.register_instance("2")

    def unregister(self):
        self.unregister_instance("1")
        self.unregister_instance("2")

    def create_containers(self):
        self.create_container("1")
        self.create_container("2")

    def remove_containers(self):
        self.remove_container("1")
        self.remove_container("2")

    def remove_blueprint(self):
        consul_obj = consul.Consul(host=global_env.consul_host,
                                   token=global_env.consul_acl_token)
        kv = consul_obj.kv

        logging.info("Removing blueprint '%s'", self.group_id)

        kv.delete("tarantool/%s/blueprint" % self.group_id,
                  recurse=True)

    def enable_replication(self):
        port = 3301

        blueprint = self.blueprint
        allocation = self.allocation

        for instance_num in allocation['instances']:
            other_instances = \
                set(allocation['instances'].keys()) - set([instance_num])

            addr = blueprint['instances'][instance_num]['addr']
            other_addrs = [blueprint['instances'][i]['addr']
                           for i in other_instances]
            docker_host = allocation['instances'][instance_num]['host']
            docker_hosts = Sense.docker_hosts()

            logging.info("Enabling replication between '%s' and '%s'",
                         addr, str(other_addrs))

            docker_addr = None
            for host in docker_hosts:
                if host['addr'].split(':')[0] == docker_host or \
                   host['consul_host'] == docker_host:
                    docker_addr = host['addr']


            docker_obj = docker.Client(base_url=docker_addr,
                                       tls=global_env.docker_tls_config)

            cmd = "tarantool_set_config.lua TARANTOOL_REPLICATION_SOURCE " + \
                  ",".join(other_addrs)

            exec_id = docker_obj.exec_create(self.group_id + '_' + instance_num,
                                             cmd)
            stream = docker_obj.exec_start(exec_id, stream=True)

            for line in stream:
                logging.info("Exec: %s", str(line))

            ret = docker_obj.exec_inspect(exec_id)

            if ret['ExitCode'] != 0:
                raise RuntimeError("Failed to enable replication for group " +
                                   self.group_id)


    def register_instance(self, instance_num):
        blueprint = self.blueprint
        allocation = self.allocation

        instance_id = self.group_id + '_' + instance_num
        docker_host = allocation['instances'][instance_num]['host']
        docker_hosts = Sense.docker_hosts()
        consul_host = None
        for host in docker_hosts:
            if host['addr'].split(':')[0] == docker_host or \
               host['consul_host'] == docker_host:
                consul_host = host['consul_host']
        if not consul_host:
            raise RuntimeError("Failed to find consul host of %s" % docker_host)

        addr = blueprint['instances'][instance_num]['addr']
        check_period = blueprint['check_period']

        consul_obj = consul.Consul(host=consul_host,
                                   token=global_env.consul_acl_token)

        replication_check = {
            'docker_container_id': instance_id,
            'shell': "/bin/sh",
            'script': "/var/lib/mon.d/tarantool_replication.sh",
            'interval': "%ds" % check_period,
            'status' : 'warning'
        }

        memory_check = {
            'docker_container_id': instance_id,
            'shell': "/bin/sh",
            'script': "/var/lib/mon.d/tarantool_memory.sh",
            'interval': "%ds" % check_period,
            'status' : 'warning'
        }

        logging.info("Registering instance '%s' on '%s'",
                     instance_id,
                     consul_host)

        ret = consul_obj.agent.service.register("memcached",
                                                service_id=instance_id,
                                                address=addr,
                                                port=3301,
                                                check=replication_check,
                                                tags=['tarantool'])

        ret = consul_obj.agent.check.register("Memory Utilization",
                                              check=memory_check,
                                              check_id=instance_id + '_memory',
                                              service_id=instance_id)


    def unregister_instance(self, instance_num):
        services = self.services
        allocation = self.allocation

        if instance_num not in services['instances']:
            return

        instance_id = self.group_id + '_' + instance_num

        docker_host = allocation['instances'][instance_num]['host']
        docker_hosts = Sense.docker_hosts()
        consul_host = None
        for host in docker_hosts:
            if host['addr'].split(':')[0] == docker_host or \
               host['consul_host'] == docker_host:
                consul_host = host['consul_host']
        if not consul_host:
            raise RuntimeError("Failed to find consul host of %s" % docker_host)

        consul_hosts = [h['addr'].split(':')[0] for h in Sense.consul_hosts()
                        if h['status'] == 'passing']

        if services:
            if consul_host in consul_hosts:
                consul_obj = consul.Consul(host=consul_host,
                                           token=global_env.consul_acl_token)

                check_id = instance_id + '_memory'
                logging.info("Unregistering check '%s'", check_id)
                consul_obj.agent.check.deregister(check_id)

                logging.info("Unregistering instance '%s' from '%s'",
                             instance_id,
                             consul_host)
                consul_obj.agent.service.deregister(instance_id)

        else:
            logging.info("Not unregistering '%s', as it's not registered",
                         instance_id)


    def create_container(self, instance_num):
        blueprint = self.blueprint
        allocation = self.allocation

        instance_id = self.group_id + '_' + instance_num
        addr = blueprint['instances'][instance_num]['addr']
        memsize = blueprint['memsize']
        network_settings = Sense.network_settings()
        network_name = network_settings['network_name']
        if not network_name:
            raise RuntimeError("Network name is not specified in settings")

        docker_host = allocation['instances'][instance_num]['host']
        docker_hosts = Sense.docker_hosts()

        docker_addr = None
        for host in docker_hosts:
            if host['addr'].split(':')[0] == docker_host or \
               host['consul_host'] == docker_host:
                docker_addr = host['addr']

        if not docker_addr:
            raise RuntimeError("No such Docker host: '%s'" % docker_host)

        replica_ip = None
        if instance_num == '2':
            replica_ip = blueprint['instances']['1']['addr']

        docker_obj = docker.Client(base_url=docker_addr,
                                   tls=global_env.docker_tls_config)

        self.ensure_image(docker_addr)
        self.ensure_network(docker_addr)

        if not replica_ip:
            logging.info("Creating memcached '%s' on '%s' with ip '%s'",
                         instance_id, docker_obj.base_url, addr)
        else:
            logging.info("Creating memcached '%s' on '%s' with ip '%s'" +
                         " and replication source: '%s'",
                         instance_id, docker_obj.base_url, addr, replica_ip)

        host_config = docker_obj.create_host_config(
            restart_policy =
            {
                "MaximumRetryCount": 0,
                "Name": "unless-stopped"
            })

        cmd = 'tarantool /opt/tarantool/app.lua'

        networking_config = {
            'EndpointsConfig':
            {
                network_name:
                {
                    'IPAMConfig':
                    {
                        "IPv4Address": addr,
                        "IPv6Address": ""
                    },
                    "Links": [],
                    "Aliases": []
                }
            }
        }

        environment = {}

        environment['TARANTOOL_SLAB_ALLOC_ARENA'] = memsize

        if replica_ip:
            environment['TARANTOOL_REPLICATION_SOURCE'] = replica_ip + ':3301'

        container = docker_obj.create_container(image='tarantool-cloud-memcached',
                                                name=instance_id,
                                                command=cmd,
                                                host_config=host_config,
                                                networking_config=networking_config,
                                                environment=environment,
                                                labels=['tarantool'])

        docker_obj.connect_container_to_network(container.get('Id'),
                                                network_name,
                                                ipv4_address=addr)
        docker_obj.start(container=container.get('Id'))

    def upgrade_container(self, instance_num):
        group_id = self.group_id

        logging.info("Upgrading container '%s'", group_id)

        blueprint = self.blueprint
        allocation = self.allocation

        instance_id = self.group_id + '_' + instance_num
        addr = blueprint['instances'][instance_num]['addr']
        memsize = blueprint['memsize']
        network_settings = Sense.network_settings()
        network_name = network_settings['network_name']
        if not network_name:
            raise RuntimeError("Network name is not specified in settings")

        docker_host = allocation['instances'][instance_num]['host']
        docker_hosts = Sense.docker_hosts()

        docker_addr = None
        for host in docker_hosts:
            if host['addr'].split(':')[0] == docker_host or \
               host['consul_host'] == docker_host:
                docker_addr = host['addr']

        if not docker_addr:
            raise RuntimeError("No such Docker host: '%s'" % docker_host)

        replica_ip = None
        if instance_num == '2':
            replica_ip = blueprint['instances']['1']['addr']

        docker_obj = docker.Client(base_url=docker_addr,
                                   tls=global_env.docker_tls_config)

        self.ensure_image(docker_addr)
        self.ensure_network(docker_addr)

        mounts = docker_obj.inspect_container(instance_id)["Mounts"]
        binds = []
        for mount in mounts:
            if mount['Destination'] == '/opt/tarantool':
                # code should be upgraded along with container
                continue

            logging.info("Keeping mount %s:%s",
                         mount["Source"], mount["Destination"])
            rw_flag = "rw" if mount['RW'] else "ro"
            binds.append("%s:%s:%s" % (mount['Source'],
                                       mount['Destination'],
                                       rw_flag))

        docker_obj.stop(container=instance_id)
        docker_obj.remove_container(container=instance_id)

        host_config = docker_obj.create_host_config(
            restart_policy =
            {
                "MaximumRetryCount": 0,
                "Name": "unless-stopped"
            },
            binds = binds
        )

        cmd = 'tarantool /opt/tarantool/app.lua'

        networking_config = {
            'EndpointsConfig':
            {
                network_name:
                {
                    'IPAMConfig':
                    {
                        "IPv4Address": addr,
                        "IPv6Address": ""
                    },
                    "Links": [],
                    "Aliases": []
                }
            }
        }

        environment = {}

        environment['TARANTOOL_SLAB_ALLOC_ARENA'] = memsize

        if replica_ip:
            environment['TARANTOOL_REPLICATION_SOURCE'] = replica_ip + ':3301'

        container = docker_obj.create_container(image='tarantool-cloud-memcached',
                                                name=instance_id,
                                                command=cmd,
                                                host_config=host_config,
                                                networking_config=networking_config,
                                                environment=environment,
                                                labels=['tarantool'])

        docker_obj.connect_container_to_network(container.get('Id'),
                                                network_name,
                                                ipv4_address=addr)
        docker_obj.start(container=container.get('Id'))

    def remove_container(self, instance_num):
        containers = self.containers

        if instance_num not in containers['instances']:
            return

        instance_id = self.group_id + '_' + instance_num
        docker_hosts = [h['addr'].split(':')[0] for h in Sense.docker_hosts()
                        if h['status'] == 'passing']

        if containers:
            docker_host = containers['instances'][instance_num]['host']
            docker_hosts = Sense.docker_hosts()

            docker_addr = None
            for host in docker_hosts:
                if host['addr'].split(':')[0] == docker_host or \
                   host['consul_host'] == docker_host:
                    docker_addr = host['addr']
            if not docker_addr:
                raise RuntimeError("No such Docker host: '%s'" % docker_host)

            logging.info("Removing container '%s' from '%s'",
                         instance_id,
                         docker_host)

            docker_obj = docker.Client(base_url=docker_addr,
                                       tls=global_env.docker_tls_config)
            docker_obj.stop(container=instance_id)
            docker_obj.remove_container(container=instance_id)
        else:
            logging.info("Not removing container '%s', as it doesn't exist",
                         instance_id)

    def resize_instance(self, instance_num, memsize):
        containers = self.containers

        if instance_num not in containers['instances']:
            return

        instance_id = self.group_id + '_' + instance_num
        docker_hosts = [h['addr'].split(':')[0] for h in Sense.docker_hosts()
                        if h['status'] == 'passing']

        if containers:
            docker_host = containers['instances'][instance_num]['host']
            docker_hosts = Sense.docker_hosts()

            docker_addr = None
            for host in docker_hosts:
                if host['addr'].split(':')[0] == docker_host or \
                   host['consul_host'] == docker_host:
                    docker_addr = host['addr']
            if not docker_addr:
                raise RuntimeError("No such Docker host: '%s'" % docker_host)

            logging.info("Resizing container '%s' to %f GiB on '%s'",
                         instance_id,
                         memsize,
                         docker_host)

            docker_obj = docker.Client(base_url=docker_addr,
                                       tls=global_env.docker_tls_config)

            cmd = "tarantool_set_config.lua TARANTOOL_SLAB_ALLOC_ARENA " + \
                  str(memsize)

            exec_id = docker_obj.exec_create(self.group_id + '_' + instance_num,
                                             cmd)
            docker_obj.exec_start(exec_id)
            ret = docker_obj.exec_inspect(exec_id)

            if ret['ExitCode'] != 0:
                raise RuntimeError("Failed to set memory size for container " +
                                   instance_id)

            docker_obj.restart(container=instance_id)
        else:
            logging.info("Not resizing container '%s', as it doesn't exist",
                         instance_id)


    def ensure_image(self, docker_addr):
        docker_obj = docker.Client(base_url=docker_addr,
                                   tls=global_env.docker_tls_config)
        image_exists = any(['tarantool-cloud-memcached:latest' in i['RepoTags']
                            for i in docker_obj.images()])

        if image_exists:
            return

        response = docker_obj.build(path='docker/tarantool-cloud-memcached',
                                    rm=True,
                                    tag='tarantool-cloud-memcached',
                                    dockerfile='Dockerfile')

        for line in response:
            decoded_line = json.loads(line.decode('utf-8'))
            if 'stream' in decoded_line:
                logging.info("Build memcached on %s: %s",
                             docker_addr,
                             decoded_line['stream'])

    def ensure_network(self, docker_addr):
        docker_obj = docker.Client(base_url=docker_addr,
                                   tls=global_env.docker_tls_config)

        settings = Sense.network_settings()
        network_name = settings['network_name']
        subnet = settings['subnet']

        if not network_name:
            raise RuntimeError("Network name not specified")

        network_exists = any([n['Name'] == network_name
                              for n in docker_obj.networks()])

        if network_exists:
            return

        if not settings['create_automatically']:
            raise RuntimeError(("No network '%s' exists and automatic creation" +
                                "prohibited") % network_name)

        ipam_pool = docker.utils.create_ipam_pool(
            subnet=subnet
        )
        ipam_config = docker.utils.create_ipam_config(
            pool_configs=[ipam_pool]
        )

        logging.info("Creating network '%s'", network_name)
        docker_obj.create_network(name=network_name,
                                  driver='bridge',
                                  ipam=ipam_config)
