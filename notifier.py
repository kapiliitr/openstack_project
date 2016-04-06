from ceilometer.compute.notifications import cpu
from ceilometer import sample

class IpmiPower(cpu.ComputeMetricsNotificationBase):
    metric = 'ipmi.power'
    unit = 'W'
    sample_type = sample.TYPE_GAUGE

class GPUPower(cpu.ComputeMetricsNotificationBase):
    metric = 'gpu.power'
    unit = 'W'
    sample_type = sample.TYPE_GAUGE

class AvgCPUFrequency(cpu.ComputeMetricsNotificationBase):
    metric = 'average.cpu.frequency'
    unit = 'GHz'
    sample_type = sample.TYPE_GAUGE

class AvgCPUTemperature(cpu.ComputeMetricsNotificationBase):
    metric = 'average.cpu.temperature'
    unit = 'C'
    sample_type = sample.TYPE_GAUGE
