import ConfigParser
import argparse
import base64
import fnmatch
import json
import os
import shutil
import subprocess
import urllib2
import requests
from urllib2 import URLError

import jinja2
import time

import tools.utility.log as log
from tools.utility import yangParser
from tools.utility.util import get_curr_dir

LOGGER = log.get_logger('statistics')

NS_MAP = {
    "http://cisco.com/ns/yang/": "cisco",
    "http://www.huawei.com/netconf": "huawei",
    "http://openconfig.net/yang/": "openconfig",
    "http://tail-f.com/": "tail-f"
}
MISSING_ELEMENT = 'missing%20element'


def find_first_file(directory, pattern, pattern_with_revision):
    """Search for yang file on path
        Arguments:
            :param directory: (str) directory which should be search recursively
                for specified file.
            :param pattern: (str) name of the yang file without revision
            :param pattern_with_revision: (str) name of the yang file with
                revision
            :return path to a searched yang file
    """

    for root, dirs, files in os.walk(directory):
        for basename in files:
            if fnmatch.fnmatch(basename, pattern_with_revision):
                filename = os.path.join(root, basename)
                return filename
    for root, dirs, files in os.walk(directory):
        for basename in files:
            if fnmatch.fnmatch(basename, pattern):
                filename = os.path.join(root, basename)
                return filename


def http_request(path, method, json_data, http_credentials, header):
    try:
        opener = urllib2.build_opener(urllib2.HTTPHandler)
        request = urllib2.Request(path, data=json_data)
        request.add_header('Content-Type', header)
        request.add_header('Accept', header)
        base64string = base64.b64encode('%s:%s' % (http_credentials[0], http_credentials[1]))
        request.add_header("Authorization", "Basic %s" % base64string)
        request.get_method = lambda: method
        return opener.open(request)
    except urllib2.HTTPError as e:
        if method == 'DELETE':
            return
        LOGGER.error('Could not send request with body {} and path {}'.format(json_data, path))
        if e.code == 404:
            return None
    except URLError as e:
        LOGGER.error('Could not send request with body {} and path {} {}'.format(json_data, path, e))
        raise e


def render(tpl_path, context):
    """Render jinja html template
        Arguments:
            :param tpl_path: (str) path to a file
            :param context: (dict) dictionary containing data to render jinja
                template file
            :return: string containing rendered html file
    """

    path, filename = os.path.split(tpl_path)
    return jinja2.Environment(
        loader=jinja2.FileSystemLoader(path or './')
    ).get_template(filename).render(context)


def list_of_yang_modules_in_subdir(srcdir):
    """
    Returns the list of YANG Modules (.yang) in all sub-directories
        Arguments
            :param srcdir: (str) root directory to search for yang files
            :return: list of YANG files
    """
    ll = []
    for root, dirs, files in os.walk(srcdir):
        for f in files:
            if f.endswith(".yang"):
                ll.append(os.path.join(root, f))
    return ll


def get_specifics(path_dir):
    """Get amount of yang files in specified directory and the amount that
    passed compilation
        Arguments:
            :param path_dir: (str) path to directory where we are searching for
                yang files
            :return: list containing amount of yang files and amount that pass
                compilation respectively
    """
    passed = 0
    num_in_catalog = 0
    for mod_git in list_of_yang_modules_in_subdir(path_dir):
        try:
            revision = yangParser.parse(os.path.abspath(mod_git)).search('revision')[0].arg
        except:
            continue
        organization = resolve_organization(mod_git)
        name = mod_git.split('/')[-1].split('.')[0].split('@')[0]
        if revision is None:
            revision = '1500-01-01'
        if name is None or organization is None:
            continue
        if ',' in organization:
            organization = organization.replace(' ', '%20')
            path = yangcatalog_api_prefix + 'search/name/' + name
            module_exist = http_request(path, 'GET', '', credentials.split(' '), 'application/vnd.yang.data+json')
            if module_exist:
                data = module_exist.read()
                org = json.loads(data)['yang-catalog:modules']['module'][0]['organization']
                rev = json.loads(data)['yang-catalog:modules']['module'][0]['revision']
                status = json.loads(data)['yang-catalog:modules']['module'][0]['compilation-status']
                if org == organization and rev == revision:
                    if 'passed' == status:
                        passed += 1
                    num_in_catalog += 1
        else:
            organization = organization.replace(' ', '%20')
            path = yangcatalog_api_prefix + 'search/modules/' + name + ','\
                   + revision + ',' + organization
            module_exist = http_request(path, 'GET', '', credentials.split(' '), 'application/vnd.yang.data+json')
            if module_exist:
                data = module_exist.read()
                if 'passed' == json.loads(data)['module'][0]['compilation-status']:
                    passed += 1
                num_in_catalog += 1
    return [num_in_catalog, passed]


def resolve_organization(path):
    """Parse yang file and resolve organization out of the module. If the module
    is a submodule find it's parent and resolve its organization
            Arguments: 
                :param path: (str) path to a file to parse and resolve a organization
                :return: list containing amount of yang files and amount that pass
                    compilation respectively
    """
    organization = ''
    try:
        namespace = yangParser.parse(os.path.abspath(path)) \
            .search('namespace')[0].arg

        for ns, org in NS_MAP.items():
            if ns in namespace:
                organization = org
        if organization == '':
            if 'urn:' in namespace:
                organization = namespace.split('urn:')[1].split(':')[0]
        if organization == '':
            organization = MISSING_ELEMENT
    except:
        while True:
            try:
                belongs_to = yangParser.parse(os.path.abspath(path)) \
                    .search('belongs-to')[0].arg
            except:
                break
            try:
                yang_file = find_first_file('/'.join(path.split('/')[:-1]), belongs_to + '.yang'
                                            , belongs_to + '@*.yang')
                namespace = yangParser.parse(os.path.abspath(yang_file)).search('namespace')[0].arg
                for ns, org in NS_MAP.items():
                    if ns in namespace:
                        organization = org
                if organization == '':
                    if 'urn:' in namespace:
                        organization = namespace.split('urn:')[1].split(':')[0]
                if organization == '':
                    organization = MISSING_ELEMENT
                break
            except:
                try:
                    yang_file = find_first_file('/'.join(path.split('/')[:-2]), belongs_to + '.yang'
                                            , belongs_to + '@*.yang')
                    namespace = yangParser.parse(os.path.abspath(yang_file)).search('namespace')[0].arg
                    for ns, org in NS_MAP.items():
                        if ns in namespace:
                            organization = org
                    if organization == '':
                        if 'urn:' in namespace:
                            organization = namespace.split('urn:')[1].split(':')[0]
                    if organization == '':
                        organization = MISSING_ELEMENT
                    break
                except:
                    organization = MISSING_ELEMENT
    return organization


def process_data(out, save_list, path, name):
    """Process all the data out of output from runYANGallstats and Yang files themself
        Arguments: 
            :param out: (str) output from runYANGallstats
            :param save_list: (list) list to which we are saving all the informations 
            :param path: (str) path to a directory to which we are creating statistics
            :param name: (str) name of the vendor or organization that we are creating
                statistics for
    """
    LOGGER.info('Getting info from {}'.format(name))
    table_sdo = {}
    modules = out.split(path + ' : ')[1].split('\n')[0]
    num_in_catalog, passed = get_specifics(path)
    table_sdo['name'] = name
    table_sdo['num_gituhub'] = modules
    table_sdo['num_catalog'] = num_in_catalog
    try:
        table_sdo['percentage_compile'] = repr(round((float(passed) / num_in_catalog) * 100, 2)) + ' %'
    except ZeroDivisionError:
        table_sdo['percentage_compile'] = 0
    table_sdo['percentage_extra'] = 'unknown'
    save_list.append(table_sdo)


if __name__ == '__main__':
    LOGGER.info('Starting statistics')
    parser = argparse.ArgumentParser()
    parser.add_argument('--config-path', type=str, default='../utility/config.ini',
                        help='Set path to config file')
    LOGGER.info('Loading all configuration')
    args = parser.parse_args()
    config_path = os.path.abspath('.') + '/' + args.config_path
    config = ConfigParser.ConfigParser()
    config.read(config_path)
    protocol = config.get('General-Section', 'protocol-api')
    api_ip = config.get('Statistics-Section', 'api-ip')
    api_port = config.get('General-Section', 'api-port')
    credentials = config.get('General-Section', 'credentials')
    move_to = config.get('Statistics-Section', 'file-location')
    auth = credentials.split(' ')
    is_uwsgi = config.get('General-Section', 'uwsgi')
    separator = ':'
    suffix = api_port
    if is_uwsgi == 'True':
        separator = '/'
        suffix = 'api'
    yangcatalog_api_prefix = '{}://{}{}{}/'.format(protocol, api_ip,
                                                   separator, suffix)

    path = yangcatalog_api_prefix + 'search/vendors/vendor/cisco'
    res = requests.get(path, auth=(auth[0], auth[1]),
                 headers={'Accept': 'application/json'})
    vendors_data = json.loads(res.content)
    xr = []
    nx = []
    xe = []
    xr_versions = sorted(next(os.walk(get_curr_dir(__file__) + '/../../vendor/cisco/xr'))[1])
    nx_versions = sorted(next(os.walk(get_curr_dir(__file__) + '/../../vendor/cisco/nx'))[1])
    xe_versions = sorted(next(os.walk(get_curr_dir(__file__) + '/../../vendor/cisco/xe'))[1])
    xr_values = []
    nx_values = []
    xe_values = []
    for vendor in vendors_data['yang-catalog:vendor']['platforms']['platform']:
        platform_name = vendor['name']
        os_type = vendor['software-versions']['software-version'][0]['software-flavors']['software-flavor'][0]\
            ['modules']['module'][0]['os-type']
        if 'IOS-XR' == os_type:
            xr.append(platform_name)
        if 'IOS-XE' == os_type:
            xe.append(platform_name)
        if 'NX-OS' == os_type:
            nx.append(platform_name)
    for version in xr_versions:
        j = None
        try:
            with open('../../vendor/cisco/xr/' + version + '/platform-metadata.json', 'r') as f:
                j = json.load(f)
                j = j['platforms']['platform']
        except:
            j = []

        values = [version]
        for value in xr:
            if version == '':
                ver = ''
            else:
                ver = '.'.join(version)
            found = False
            for platform in j:
                if (platform['name'] == value and
                            platform['software-version'] == ver):
                    values.append('<i class="fa fa-check"></i>')
                    found = True
                    break
            if not found:
                values.append('<i class="fa fa-times"></i>')
        xr_values.append(values)

    for version in xe_versions:
        j = None
        try:
            with open('../../vendor/cisco/xe/' + version + '/platform-metadata.json', 'r') as f:
                j = json.load(f)
                j = j['platforms']['platform']
        except:
            j = []
        values = [version]
        for value in xe:
            found = False
            for platform in j:
                if (platform['name'] == value and
                            platform['software-version'] == version):
                    values.append('<i class="fa fa-check"></i>')
                    found = True
                    break
            if not found:
                values.append('<i class="fa fa-times"></i>')
        xe_values.append(values)

    for version in nx_versions:
        j = None
        try:
            with open('../../vendor/cisco/nx/' + version + '/platform-metadata.json', 'r') as f:
                j = json.load(f)
                j = j['platforms']['platform']
        except:
            j = []
        values = [version]
        for value in nx:
            ver = version.split('-')
            ver = '{}({}){}({})'.format(ver[0], ver[1], ver[2], ver[3])
            found = False
            for platform in j:
                if (platform['name'] == value and
                            platform['software-version'] == ver):
                    values.append('<i class="fa fa-check"></i>')
                    found = True
                    break
            if not found:
                values.append('<i class="fa fa-times"></i>')
        nx_values.append(values)

    path = yangcatalog_api_prefix + 'search/modules'
    all_modules_data = (requests.get(path, auth=(auth[0], auth[1]),
                                     headers={'Accept': 'application/json'})
                        .content)
    all_modules_data = json.loads(all_modules_data)
    all_modules_data_unique = set()
    for mod in all_modules_data['module']:
        name = mod['name']
        revision = mod['revision']
        all_modules_data_unique.add('{}@{}'.format(name, revision))
    all_modules_data = len(all_modules_data['module'])

    # Vendors sparately
    vendor_list = []

    process = subprocess.Popen(['python', '../runYANGallstats/runYANGallstats.py', '--rootdir', get_curr_dir(__file__) + '/../../vendor/cisco',
                                '--removedup', 'True'], stdout=subprocess.PIPE)
    out, err = process.communicate()
    process_data(out, vendor_list, get_curr_dir(__file__) + '/../../vendor/cisco', 'cisco')

    process = subprocess.Popen(['python', '../runYANGallstats/runYANGallstats.py', '--rootdir', get_curr_dir(__file__) + '/../../vendor/ciena',
                                '--removedup', 'True'], stdout=subprocess.PIPE)
    out, err = process.communicate()
    process_data(out, vendor_list, get_curr_dir(__file__) + '/../../vendor/ciena', 'ciena')

    process = subprocess.Popen(['python', '../runYANGallstats/runYANGallstats.py', '--rootdir', get_curr_dir(__file__) + '/../../vendor/juniper',
                                '--removedup', 'True'], stdout=subprocess.PIPE)
    out, err = process.communicate()
    process_data(out, vendor_list, get_curr_dir(__file__) + '/../../vendor/juniper', 'juniper')

    process = subprocess.Popen(['python', '../runYANGallstats/runYANGallstats.py', '--rootdir', get_curr_dir(__file__) + '/../../vendor/huawei',
                                '--removedup', 'True'], stdout=subprocess.PIPE)
    out, err = process.communicate()
    process_data(out, vendor_list, get_curr_dir(__file__) + '/../../vendor/huawei', 'huawei')

    # Vendors all together
    process = subprocess.Popen(['python', '../runYANGallstats/runYANGallstats.py', '--rootdir', get_curr_dir(__file__) + '/../../vendor',
                                '--removedup', 'True'], stdout=subprocess.PIPE)
    out, err = process.communicate()
    vendor_modules = out.split(get_curr_dir(__file__) + '/../../vendor : ')[1].split('\n')[0]
    vendor_modules_ndp = out.split(get_curr_dir(__file__) + '/../../vendor (duplicates removed): ')[1].split('\n')[0]

    # Standard all together
    process = subprocess.Popen(['python', '../runYANGallstats/runYANGallstats.py', '--rootdir', get_curr_dir(__file__) + '/../../standard',
                                '--removedup', 'True'], stdout=subprocess.PIPE)
    out, err = process.communicate()
    standard_modules = out.split(get_curr_dir(__file__) + '/../../standard : ')[1].split('\n')[0]
    standard_modules_ndp = out.split(get_curr_dir(__file__) + '/../../standard (duplicates removed): ')[1].split('\n')[0]

    # Standard separately
    sdo_list = []
    process = subprocess.Popen(['python', '../runYANGallstats/runYANGallstats.py', '--rootdir',
                                get_curr_dir(__file__) + '/../../standard/ietf/RFC', '--removedup', 'True'], stdout=subprocess.PIPE)
    out, err = process.communicate()
    process_data(out, sdo_list, get_curr_dir(__file__) + '/../../standard/ietf/RFC', 'IETF RFCs')

    process = subprocess.Popen(['python', '../runYANGallstats/runYANGallstats.py', '--rootdir',
                                get_curr_dir(__file__) + '/../../standard/ietf/DRAFT', '--removedup', 'True'], stdout=subprocess.PIPE)
    out, err = process.communicate()
    process_data(out, sdo_list, get_curr_dir(__file__) + '/../../standard/ietf/DRAFT', 'IETF drafts')

    process = subprocess.Popen(['python', '../runYANGallstats/runYANGallstats.py', '--rootdir',
                                get_curr_dir(__file__) + '/../../standard/bbf/standard', '--removedup', 'True'], stdout=subprocess.PIPE)
    out, err = process.communicate()
    process_data(out, sdo_list, get_curr_dir(__file__) + '/../../standard/bbf/standard', 'BBF standard')

    process = subprocess.Popen(['python', '../runYANGallstats/runYANGallstats.py', '--rootdir',
                                get_curr_dir(__file__) + '/../../standard/bbf/draft', '--removedup', 'True'], stdout=subprocess.PIPE)
    out, err = process.communicate()
    process_data(out, sdo_list, get_curr_dir(__file__) + '/../../standard/bbf/draft', 'BBF draft')

    process = subprocess.Popen(['python', '../runYANGallstats/runYANGallstats.py', '--rootdir',
                                get_curr_dir(__file__) + '/../../standard/ieee/802.1', '--removedup', 'True'], stdout=subprocess.PIPE)
    out, err = process.communicate()
    process_data(out, sdo_list, get_curr_dir(__file__) + '/../../standard/ieee/802.1', 'IEEE 802.1 with par')

    process = subprocess.Popen(['python', '../runYANGallstats/runYANGallstats.py', '--rootdir',
                                get_curr_dir(__file__) + '/../../experimental/ieee/802.1', '--removedup', 'True'], stdout=subprocess.PIPE)
    out, err = process.communicate()
    process_data(out, sdo_list, get_curr_dir(__file__) + '/../../experimental/ieee/802.1', 'IEEE 802.1 no par')

    process = subprocess.Popen(['python', '../runYANGallstats/runYANGallstats.py', '--rootdir',
                                get_curr_dir(__file__) + '/../../standard/ieee/802.3', '--removedup', 'True'], stdout=subprocess.PIPE)
    out, err = process.communicate()
    process_data(out, sdo_list, get_curr_dir(__file__) + '/../../standard/ieee/802.3', 'IEEE 802.3 with par')

    process = subprocess.Popen(['python', '../runYANGallstats/runYANGallstats.py', '--rootdir',
                                get_curr_dir(__file__) + '/../../experimental/ieee/802.3', '--removedup', 'True'], stdout=subprocess.PIPE)
    out, err = process.communicate()
    process_data(out, sdo_list, get_curr_dir(__file__) + '/../../experimental/ieee/802.3', 'IEEE 802.3 no par')

    process = subprocess.Popen(['python', '../runYANGallstats/runYANGallstats.py', '--rootdir',
                                get_curr_dir(__file__) + '/../../standard/ieee/draft', '--removedup', 'True'], stdout=subprocess.PIPE)
    out, err = process.communicate()
    process_data(out, sdo_list, get_curr_dir(__file__) + '/../../standard/ieee/draft', 'IEEE draft with par')

    process = subprocess.Popen(['python', '../runYANGallstats/runYANGallstats.py', '--rootdir',
                                get_curr_dir(__file__) + '/../../standard/mef/src/model/standard', '--removedup', 'True'], stdout=subprocess.PIPE)
    out, err = process.communicate()
    process_data(out, sdo_list, get_curr_dir(__file__) + '/../../standard/mef/src/model/standard', 'MEF standard')

    process = subprocess.Popen(['python', '../runYANGallstats/runYANGallstats.py', '--rootdir',
                                get_curr_dir(__file__) + '/../../standard/mef/src/model/draft', '--removedup', 'True'], stdout=subprocess.PIPE)
    out, err = process.communicate()
    process_data(out, sdo_list, get_curr_dir(__file__) + '/../../standard/mef/src/model/draft', 'MEF draft')

    process = subprocess.Popen(['python', '../runYANGallstats/runYANGallstats.py', '--rootdir',
                                get_curr_dir(__file__) + '/../../experimental/openconfig', '--removedup', 'True'], stdout=subprocess.PIPE)
    out, err = process.communicate()
    process_data(out, sdo_list, get_curr_dir(__file__) + '/../../experimental/openconfig', 'openconfig')

    context = {'table_sdo': sdo_list,
               'table_vendor': vendor_list,
               'num_yang_files_vendor': vendor_modules,
               'num_yang_files_vendor_ndp': vendor_modules_ndp,
               'num_yang_files_standard': standard_modules,
               'num_yang_files_standard_ndp': standard_modules_ndp,
               'num_parsed_files': all_modules_data,
               'num_unique_parsed_files': len(all_modules_data_unique),
               'nx': nx,
               'xr': xr,
               'xe': xe,
               'nx_values': nx_values,
               'xe_values': xe_values,
               'xr_values': xr_values,
               'current_date': time.strftime("%d/%m/%y")}
    LOGGER.info('Rendering data')
    result = render('./template/stats.html', context)
    with open('./statistics.html', 'w+') as f:
        f.write(result)

    file_from = os.path.abspath('./statistics.html')
    file_to = os.path.abspath(move_to) + '/statistics.html'
    if move_to != './':
        if os.path.exists(file_to):
            os.remove(file_to)
        shutil.move(file_from, file_to)
