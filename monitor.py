from oslo_config import cfg
from oslo_log import log as logging
from oslo_utils import timeutils
from oslo_concurrency import processutils

from nova.compute import monitors
from nova.compute.monitors import cpu_monitor as monitor
from nova import exception
from nova.i18n import _LE
from nova import utils

CONF = cfg.CONF
CONF.import_opt('compute_driver', 'nova.virt.driver')
LOG = logging.getLogger(__name__)


class ComputeDriverPowerMonitor(monitors.ResourceMonitorBase):

    def __init__(self, parent):
        super(ComputeDriverPowerMonitor, self).__init__(parent)
        self.source = CONF.compute_driver
        self.driver = self.compute_manager.driver
        self._power_stats = {}


    @monitors.ResourceMonitorBase.add_timestamp
    def _get_ipmi_power(self, **kwargs):
        return self._data.get("ipmi.power")

    @monitors.ResourceMonitorBase.add_timestamp
    def _get_gpu_power(self, **kwargs):
        return self._data.get("gpu.power")

    @monitors.ResourceMonitorBase.add_timestamp
    def _get_average_cpu_frequency(self, **kwargs):
        return self._data.get("average.cpu.frequency")

    @monitors.ResourceMonitorBase.add_timestamp
    def _get_average_cpu_temperature(self, **kwargs):
        return self._data.get("average.cpu.temperature")

    def _fetch_hostname(self):
        args = ['virsh','hostname']
        (out, __) = utils.execute(*args, run_as_root=True)
        return out

    def _fetch_nodeinfo(self):
        args = ['virsh','nodeinfo']
        (out, __) = utils.execute(*args, run_as_root=True)
        node_info_dict = {}
        node_info_array = out.split('\n')
        for field in node_info_array:
            kv_value = field.split(':')
            if len(kv_value) != 2:
                continue
            node_info_dict[kv_value[0].strip()] = kv_value[1].strip()
        return node_info_dict

    def _fetch_running_instances(self):
        args = ['virsh', 'list', '--state-running']
        (out, __) = utils.execute(*args, run_as_root=True)
        instance_dict = {}
        for instance_row in out.split('\n')[2:]:
            instance = instance_row.split()
            if len(instance) != 3:
                continue
            instance_dict[int(instance[0])] = instance[1]
        return instance_dict
   
    def _fetch_cpu_info_domain(self, domain_id):
        cpu_dict = {}
        cpu_dict["timestamp"] = timeutils.utcnow_ts(microsecond=True)
        args = ['virsh', 'domstats', domain_id]
        (out, __) = utils.execute(*args, run_as_root=True)
        
        stat_dict = {}
        stat_array = out.split('\n')
        for field in stat_array:
            kv_value = field.split('=')
            if len(kv_value) != 2:
                continue
            stat_dict[kv_value[0].strip()] = kv_value[1].strip()
        nvcpus = int(stat_dict['vcpu.current'])
        total_cputime = 0
        for num in range(nvcpus):
            total_cputime += long(stat_dict['vcpu.'+str(num)+'.time'])
        cpu_dict["cputime"] = total_cputime
        return cpu_dict

    def _fetch_cpu_stats(self):
        stats = {}
        running_instances = self._fetch_running_instances()
        for instance_id in running_instances:
            stats[instance_id] = self._fetch_cpu_info_domain(instance_id)
        return stats

    def _fetch_cpu_power(self):
        args = ['ipmi-dcmi','--get-system-power-statistics']
        (out, __) = utils.execute(*args, run_as_root=True)
        dcmi_data_dict = {}
        dcmi_data_array = out.split('\n')
        for field in dcmi_data_array:
            kv_value = field.split(':')
            if len(kv_value) != 2:
                continue
            dcmi_data_dict[kv_value[0].strip()] = kv_value[1].strip()

        sensor_reading = dcmi_data_dict['Current Power']
        return float(sensor_reading.split(' ', 1)[0])

    def _fetch_gpu_power(self):
        args = ['nvidia-smi','-q','-d','POWER']
        (out, __) = utils.execute(*args, run_as_root=True)
        sensors_data = []
        sensors_data_array = out.split('\n\n') 
        for sensor_data in sensors_data_array:
            sensor_data_fields = sensor_data.split('\n')
            sensor_data_dict = {}
            for field in sensor_data_fields:
                if not field:
                    continue
                kv_value = field.split(':')
                if len(kv_value) != 2:
                    continue
                sensor_data_dict[kv_value[0].strip()] = kv_value[1].strip()
            if not sensor_data_dict:
                continue
            if 'Power Draw' in sensor_data_dict:
                sensors_data.append(sensor_data_dict)
	total_power = 0
        for gpu_data in sensors_data:
            total_power += float(gpu_data['Power Draw'].split(' ', 1)[0])
        avg_power = total_power / len(sensors_data)
        return avg_power

    def _fetch_avg_cpu_frequency(self):
        freq_data_dict = {}
        args = ['cpufreq-aperf','-o','-i','1']
        (out, __) = utils.execute(*args, run_as_root=True)
        for row in out.split('\n'):              
            data = row.split('\t\t')[0].split('\t')
            if len(data)!=2:
                continue
            index = data[0].strip('0') or '0'
            freq_data_dict['cpu'+index] = str(float(data[1])/1000000)+' GHz\n'

        total = 0
        for cpu, freq in freq_data_dict.iteritems():
            total += float(freq.split()[0])
        avg_freq = total / len(freq_data_dict)
        return avg_freq

    def _fetch_avg_cpu_temperature(self):
        args = ['ipmitool','sdr','type','Temperature']
        (out, __) = utils.execute(*args, run_as_root=True)
        total = 0
        count = 0
        for row in out.split('\n'):
            row_data = row.split('|')            
            if row_data[0].find('CPU') != -1:
                count += 1
                total += float(row_data[4].split()[0])
        avg_temp = total / count
        return avg_temp


    def _update_data(self, **kwargs):
        now = timeutils.utcnow()
        self._data = {}
        self._data["timestamp"] = now

        # Get CPU utilization per instance to split the power readings
        try:
            hostname = self._fetch_hostname()
            nr_cores = int(self._fetch_nodeinfo()['CPU(s)'])
            stats = self._fetch_cpu_stats()
        except processutils.ProcessExecutionError as e:
            LOG.exception(_LE(
                'virsh CLI not found'
                'Unable to split metrics for host \n %s'), e)

        perc_stats = {}
        for instance_id, cpu_stat in stats.iteritems():
            interval = cpu_stat["timestamp"] - self._power_stats.get(instance_id, {}).get("timestamp", 0)
            cpu_time_diff = cpu_stat["cputime"] - self._power_stats.get(instance_id, {}).get("cputime", 0)
            cpu_perc = cpu_time_diff / (interval * nr_cores * (10 ** 9))
            perc_stats[instance_id] = cpu_perc

        # Extract node's power statistics.
        try:
            self._data["ipmi.power"] = self._fetch_cpu_power()
            self._data["gpu.power"] = self._fetch_gpu_power()
            self._data["average.cpu.frequency"] = self._fetch_avg_cpu_frequency()
            self._data["average.cpu.temperature"] = self._fetch_avg_cpu_temperature()
        except (processutils.ProcessExecutionError, KeyError) as e:
            LOG.exception(_LE(
                'Power sensor failed ! '
                'Monitor for Power is disabled! \n %s'), e)
            raise exception.ResourceMonitorError(
                monitor=self.__class__.__name__)
        
        self._power_stats = stats.copy()
