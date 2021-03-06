import os
import sys
import boto3
import pickle
from joblib import Parallel, delayed
from datetime import datetime
today = datetime.now().isoformat()[:10]

class EC2:
    def __init__(self, cfg):
        self.cfg = cfg

    def _get_cluster(self):
        filters = [{'Name': 'instance-state-name', 'Values': ['running']},
                   {'Name': 'key-name', 'Values': [self.cfg['key_name']]}]
        live_instances = ec2.instances.filter(Filters=filters)

        return live_instances

    def wait_until_initialized(self):
        instances = self._get_cluster()
        for instance in instances:
            instance.wait_until_running()


    def launch(self):
        tags = [{'Key': 'cluster-name', 'Value': self.cfg['cluster']['name']}]
        instance_type = self.cfg['instance_type']
        kwargs = {'MinCount': self.cfg['cluster']['count'],
                  'MaxCount': self.cfg['cluster']['count'],
                  'KeyName': self.cfg['key_name'],
                  'InstanceType': self.cfg['cluster']['instance_type'],
                  'ImageId': self.cfg['ami'],
                  #  'Placement': {'AvailabilityZone': self.cfg['availability_zone']},
                  'SecurityGroups': self.cfg['security_groups']}
        if instance_type == 'dedicated':
            r = client.run_instances(**kwargs)
        elif instance_type == 'spot-instance':
            count = kwargs.pop('MinCount')
            kwargs.pop('MaxCount')
            spot_kwargs = {'InstanceCount': count,
                           'LaunchSpecification': kwargs,
                           'SpotPrice': self.cfg['spot_price']}
            if 'block-duration-minutes' in self.cfg['cluster'].keys():
                spot_kwargs['BlockDurationMinutes'] = self.cfg['cluster']['block-duration-minutes']
                print('Requestion block...')
            r = client.request_spot_instances(**spot_kwargs)
        else:
            msg = 'cfg["instance_type"]=={instance_type} not one in {types]'
            raise ValueError(msg.format(types=['dedicated', 'spot-instance'],
                                        instance_type=instance_type))

        self.wait_until_initialized()
        return r

    def write_public_dnss(self, filename, instances=None):
        if instances is None:
            instances = self._get_cluster()
        DNSs = [instance.public_dns_name for instance in instances]
        with open(filename, 'w') as f:
            print('\n'.join(DNSs), file=f)

    def write_private_ips(self, filename, instances=None):
        if instances is None:
            instances = self._get_cluster()
        ips = [instance.private_ip_address for instance in instances]
        with open(filename, 'w') as f:
            print('\n'.join(ips), file=f)

    def scp_up(self, instances=None, files='../WideResNet-pytorch/* hosts',
               out='WideResNet-pytorch', flags=''):
        if instances is None:
            instances = self._get_cluster()
        ips = [instance.classic_address.public_ip for instance in instances]
        cmd = ('scp -o StrictHostKeyChecking=no -i {key} {flags} {files} '
               'ec2-user@{ip}:~/{out}')
        cmds = [cmd.format(key=self.cfg['key_file'], files=files, ip=ip,
                           out=out, flags=flags)
                  for ip in ips]
        Parallel(n_jobs=len(cmds))(delayed(os.system)(run) for run in cmds)

    def run_cluster_ssh_command(self, cmd, instances=None):
        if instances is None:
            instances = self._get_cluster()
        ips = [instance.classic_address.public_ip for instance in instances]
        to_run = 'ssh -i {keyfile} ec2-user@{ip} "{cmd}"'

        commands = [to_run.format(keyfile=self.cfg['key_file'], ip=ip, cmd=cmd)
                    for ip in ips]
        print('Running on every node in cluster:')
        print(commands[0])
        Parallel(n_jobs=len(commands))(delayed(os.system)(command)
                                       for command in commands)

    def run_command_one_node(self, cmd):
        instances = self._get_cluster()
        ips = [instance.classic_address.public_ip for instance in instances]
        ip = ips[0]
        to_run = 'ssh -i {keyfile} ec2-user@{ip} "{cmd}"'
        run = to_run.format(keyfile=self.cfg['key_file'], ip=ip, cmd=cmd)
        print(run)
        os.system(run)
        return ip

    def terminate(self):
        instances = self._get_cluster()
        ids = [x.id for x in instances]
        print(ids)
        if len(ids) != 0:
            print("Terminating instances: %s" %
                  (" ".join([str(x) for x in ids])))
            client.terminate_instances(InstanceIds=ids)

    def _tag_instances(self, instances=None, **kwargs):
        if instances is None:
            instances = self._get_cluster()
        for instance in instances:
            instance.create_tags(Tags=kwargs)

    def seperate_cluster(self, size=2):
        """
        size : int
            How large each cluster is. size=2 with 16 machines corresponds to 8 groups of two machines each.
        """
        if not isinstance(size, int):
            size = int(size)
        instances = self._get_cluster()
        groups = list(chunks(list(instances), size))
        for i, group in enumerate(groups):
            #  self._tag_instances(instances=group, cluster_idx=str(i))
            self.write_private_ips(f'hosts{i}', instances=group)
            self.scp_up(files=f'./hosts{i}/', instances=group)
            print(i, group[0].public_dns_name)


def chunks(l, n):
    """Yield successive n-sized chunks from l."""
    for i in range(0, len(l), n):
        yield l[i:i + n]

cfg = {'region': 'us-west-2',# 'availability_zone': 'us-west-2c',
       'ami': 'ami-ceb545b6',
       #  'security_groups': ['sg-2d241757',  # Jupyter notebooks
                           #  'sg-f2937f8f',  # jupyter + ssh + ports{3141,6282}
                           #  'sg-491aa934',  # MPI1
                           #  'sg-ef1fac92'],  # MPI2
       'security_groups': ['Jupyter notebooks',
                           'jupyter+ssh+ports{3141,6282}',
                           'MPI1',
                           'MPI2'],
       'instance_type': 'spot-instance',
       'spot_price': '1.00',  # must be a str
       #  'instance_type': 'dedicated',
       'key_name': 'scott-key-dim',
       'key_file': '/Users/scott/Developer/AWS/scott-key-dim.pem',
       'cluster': {'count': 16, 'instance_type': 'p2.xlarge',
                   'name': 'scott-compress',
                   'block-duration-minutes': 360,
                   }
       }

client = boto3.client("ec2", region_name=cfg['region'])
ec2 = boto3.resource("ec2", region_name=cfg['region'])

if __name__ == "__main__":
    # python launch.py --seed=42 --layers=62 --compress=1
    # cluster launched at 10:30 on 2018-02-03
    cloud = EC2(cfg)
    if len(sys.argv) < 2:
        print('Usage: python {} command [custom_cluster_command]'.format(sys.argv[0]))
        sys.exit(1)
    command = sys.argv[1]
    if command == 'launch':
        cluster = cloud.launch()
        print("Lauched, now waiting to enter ssh-ready state...")
        #  cloud.wait_until_initialized()  # doesn't work
    elif command == 'write':
        cloud.write_public_dnss('DNSs')
        cloud.write_private_ips('hosts')
    elif command == 'setup':
        instances = [x.id for x in cloud._get_cluster()]
        print('About to setup...\n', instances)
        cloud.write_public_dnss('DNSs')
        cloud.write_private_ips('hosts')
        cloud.scp_up()
        cloud.run_cluster_ssh_command('pip install --upgrade distributed')
        cloud.run_cluster_ssh_command('conda install -y -c anaconda python-blosc')
        cloud.run_cluster_ssh_command('conda install -y pytorch==0.3.0 torchvision -c pytorch')
        cloud.scp_up(files='./setup_scripts/', out='', flags='-r')
        cloud.run_cluster_ssh_command('cd setup_scripts; bash main.sh')
        cloud.run_cluster_ssh_command('ssh-keyscan -f ~/WideResNet-pytorch/hosts >> ~/.ssh/known_hosts')
    elif command == 'ssh_setup':
        cloud.run_cluster_ssh_command('cd setup_scripts; bash main.sh')
        cloud.run_cluster_ssh_command('ssh-keyscan -f ~/WideResNet-pytorch/hosts >> ~/.ssh/known_hosts')
    elif command in {'scp', 'scp_up'}:
        cloud.scp_up()
        cloud.run_cluster_ssh_command('mkdir /home/ec2-user/WideResNet-pytorch/pytorch_ps_mpi')
        cloud.scp_up(files='../WideResNet-pytorch/pytorch_ps_mpi/*.py',
                     out='WideResNet-pytorch/pytorch_ps_mpi/')
        cloud.run_cluster_ssh_command('mkdir /home/ec2-user/WideResNet-pytorch/codings')
        cloud.scp_up(files='../WideResNet-pytorch/codings/*.py',
                     out='WideResNet-pytorch/codings/')
    elif command == 'debug':
        cloud.run_cluster_ssh_command('killall python')
        cloud.scp_up()
        cloud.scp_up(files='../WideResNet-pytorch/pytorch_ps_mpi/*.py',
                     out='WideResNet-pytorch/pytorch_ps_mpi/')
    elif command == 'killall':
        cloud.run_cluster_ssh_command('killall python')
    elif command in {'terminate', 'shutdown'}:
        cloud.terminate()
    elif command == 'custom':
        cmd = sys.argv[2]
        cloud.run_cluster_ssh_command(cmd)
    elif command == 'write_DNSs':
        cloud.write_public_dnss('DNSs')
    elif command == 'dask_workers':
        if len(sys.argv) != 3:
            print('Usage: python cluster.py dask_workers ip')
            sys.exit(1)
        ip = sys.argv[2]
        cloud.run_cluster_ssh_command('dask-worker --nprocs=1 --nthreads=1 '
                                      f'{ip}:8786 > ~/WideResNet-pytorch/'
                                      'dask.out 2>&1 &')
    elif command == 'kill_dask_workers':
        cloud.run_cluster_ssh_command('sudo killall python')
        cloud.run_cluster_ssh_command('sudo killall dask-worker')
    elif command == 'remove_output':
        cloud.run_cluster_ssh_command('sudo rm -rf ~/output/*')
        cloud.run_cluster_ssh_command('sudo rm -rf ~/WideResNet-pytorch/output/*')
    else:
        if command not in dir(cloud):
            raise ValueError('Usage: python launch.py [command].\n'
                             '`command` not recognized.')
        kwargs = filter(lambda x: '--' in x, sys.argv[2:])
        kwargs = map(lambda x: x.replace('--', ''), kwargs)
        kwargs = {s[:s.find('=')]: s[s.find('=') + 1:] for s in kwargs}
        getattr(cloud, command)(**kwargs)
