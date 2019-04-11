import logging
import httplib
import urllib
import hmac
import hashlib
import base64
import xmltodict
from time import gmtime, strftime
from multisite import Zone
import boto3
from botocore.client import Config

log = logging.getLogger('rgw_multi.tests')


class PSZone(Zone):  # pylint: disable=too-many-ancestors
    """ PubSub zone class """
    def is_read_only(self):
        return True

    def tier_type(self):
        return "pubsub"

    def create(self, cluster, args=None, **kwargs):
        if args is None:
            args = ''
        args += ['--tier-type', self.tier_type()]
        return self.json_command(cluster, 'create', args)

    def has_buckets(self):
        return False


NO_HTTP_BODY = ''


def print_connection_info(conn):
    """print connection details"""
    print('Endpoint: ' + conn.host + ':' + str(conn.port))
    print('AWS Access Key:: ' + conn.aws_access_key_id)
    print('AWS Secret Key:: ' + conn.aws_secret_access_key)


def make_request(conn, method, resource, parameters=None, sign_parameters=False, extra_parameters=None):
    """generic request sending to pubsub radogw
    should cover: topics, notificatios and subscriptions
    """
    url_params = ''
    if parameters is not None:
        url_params = urllib.urlencode(parameters)
        # remove 'None' from keys with no values
        url_params = url_params.replace('=None', '')
        url_params = '?' + url_params
    if extra_parameters is not None:
        url_params = url_params + '&' + extra_parameters
    string_date = strftime("%a, %d %b %Y %H:%M:%S +0000", gmtime())
    string_to_sign = method + '\n\n\n' + string_date + '\n' + resource
    if sign_parameters:
        string_to_sign += url_params
    signature = base64.b64encode(hmac.new(conn.aws_secret_access_key,
                                          string_to_sign.encode('utf-8'),
                                          hashlib.sha1).digest())
    headers = {'Authorization': 'AWS '+conn.aws_access_key_id+':'+signature,
               'Date': string_date,
               'Host': conn.host+':'+str(conn.port)}
    http_conn = httplib.HTTPConnection(conn.host, conn.port)
    if log.getEffectiveLevel() <= 10:
        http_conn.set_debuglevel(5)
    http_conn.request(method, resource+url_params, NO_HTTP_BODY, headers)
    response = http_conn.getresponse()
    data = response.read()
    status = response.status
    http_conn.close()
    return data, status


class PSTopic:
    """class to set/get/delete a topic
    PUT /topics/<topic name>[?push-endpoint=<endpoint>&[<arg1>=<value1>...]]
    GET /topics/<topic name>
    DELETE /topics/<topic name>
    """
    def __init__(self, conn, topic_name, endpoint=None, endpoint_args=None):
        self.conn = conn
        assert topic_name.strip()
        self.resource = '/topics/'+topic_name
        if endpoint is not None:
            self.parameters = {'push-endpoint': endpoint}
            self.extra_parameters = endpoint_args
        else:
            self.parameters = None
            self.extra_parameters = None

    def send_request(self, method, get_list=False, parameters=None, extra_parameters=None):
        """send request to radosgw"""
        if get_list:
            return make_request(self.conn, method, '/topics')
        return make_request(self.conn, method, self.resource, 
                            parameters=parameters, extra_parameters=extra_parameters)

    def get_config(self):
        """get topic info"""
        return self.send_request('GET')

    def set_config(self):
        """set topic"""
        return self.send_request('PUT', parameters=self.parameters, extra_parameters=self.extra_parameters)

    def del_config(self):
        """delete topic"""
        return self.send_request('DELETE')
    
    def get_list(self):
        """list all topics"""
        return self.send_request('GET', get_list=True)


class PSNotification:
    """class to set/get/delete a notification
    PUT /notifications/bucket/<bucket>?topic=<topic-name>[&events=<event>[,<event>]]
    GET /notifications/bucket/<bucket>
    DELETE /notifications/bucket/<bucket>?topic=<topic-name>
    """
    def __init__(self, conn, bucket_name, topic_name, events=''):
        self.conn = conn
        assert bucket_name.strip()
        assert topic_name.strip()
        self.resource = '/notifications/bucket/'+bucket_name
        if events.strip():
            self.parameters = {'topic': topic_name, 'events': events}
        else:
            self.parameters = {'topic': topic_name}

    def send_request(self, method, parameters=None):
        """send request to radosgw"""
        return make_request(self.conn, method, self.resource, parameters)

    def get_config(self):
        """get notification info"""
        return self.send_request('GET')

    def set_config(self):
        """set notification"""
        return self.send_request('PUT', self.parameters)

    def del_config(self):
        """delete notification"""
        return self.send_request('DELETE', self.parameters)


class PSNotificationS3:
    """class to set/get/delete an S3 notification
    PUT /<bucket>?notification
    GET /<bucket>?notification[=<notification>]
    DELETE /<bucket>?notification[=<notification>]
    """
    def __init__(self, conn, bucket_name, notification, topic_arn, events=None):
        self.conn = conn
        assert bucket_name.strip()
        self.bucket_name = bucket_name
        self.resource = '/'+bucket_name
        self.notification = notification
        self.topic_arn = topic_arn
        self.events = events
        self.client = boto3.client('s3',
                                   endpoint_url='http://'+conn.host+':'+str(conn.port),
                                   aws_access_key_id=conn.aws_access_key_id,
                                   aws_secret_access_key=conn.aws_secret_access_key,
                                   config=Config(signature_version='s3'))

    def send_request(self, method, parameters=None):
        """send request to radosgw"""
        return make_request(self.conn, method, self.resource,
                            parameters=parameters, sign_parameters=True)

    def get_config(self, all_notifications=True):
        """get notification info"""
        parameters = None
        if all_notifications:
            response = self.client.get_bucket_notification_configuration(Bucket=self.bucket_name)
            status = response['ResponseMetadata']['HTTPStatusCode']
            return response, status
        parameters = {'notification': self.notification}
        response, status = self.send_request('GET', parameters=parameters)
        dict_response = xmltodict.parse(response)
        return dict_response, status

    def set_config(self):
        """set notification"""
        response = self.client.put_bucket_notification_configuration(Bucket=self.bucket_name,
                                                                     NotificationConfiguration={
                                                                         'TopicConfigurations': [
                                                                             {
                                                                                 'Id': self.notification,
                                                                                 'TopicArn': self.topic_arn,
                                                                                 'Events': self.events,
                                                                             }
                                                                         ]
                                                                     })
        status = response['ResponseMetadata']['HTTPStatusCode']
        return response, status

    def del_config(self, all_notifications=True):
        """delete notification"""
        parameters = None
        if all_notifications:
            parameters = {'notification': None}
        else:
            parameters = {'notification': self.notification}

        return self.send_request('DELETE', parameters)


class PSSubscription:
    """class to set/get/delete a subscription:
    PUT /subscriptions/<sub-name>?topic=<topic-name>[&push-endpoint=<endpoint>&[<arg1>=<value1>...]]
    GET /subscriptions/<sub-name>
    DELETE /subscriptions/<sub-name>
    also to get list of events, and ack them:
    GET /subscriptions/<sub-name>?events[&max-entries=<max-entries>][&marker=<marker>]
    POST /subscriptions/<sub-name>?ack&event-id=<event-id>
    """
    def __init__(self, conn, sub_name, topic_name, endpoint=None, endpoint_args=None):
        self.conn = conn
        assert topic_name.strip()
        self.resource = '/subscriptions/'+sub_name
        if endpoint is not None:
            self.parameters = {'topic': topic_name, 'push-endpoint': endpoint}
            self.extra_parameters = endpoint_args
        else:
            self.parameters = {'topic': topic_name}
            self.extra_parameters = None

    def send_request(self, method, parameters=None, extra_parameters=None):
        """send request to radosgw"""
        return make_request(self.conn, method, self.resource, 
                            parameters=parameters,
                            extra_parameters=extra_parameters)

    def get_config(self):
        """get subscription info"""
        return self.send_request('GET')

    def set_config(self):
        """set subscription"""
        return self.send_request('PUT', parameters=self.parameters, extra_parameters=self.extra_parameters)

    def del_config(self, topic=False):
        """delete subscription"""
        if topic:
            return self.send_request('DELETE', self.parameters)
        return self.send_request('DELETE')

    def get_events(self, max_entries=None, marker=None):
        """ get events from subscription """
        parameters = {'events': None}
        if max_entries is not None:
            parameters['max-entries'] = max_entries
        if marker is not None:
            parameters['marker'] = marker
        return self.send_request('GET', parameters)

    def ack_events(self, event_id):
        """ ack events in a subscription """
        parameters = {'ack': None, 'event-id': event_id}
        return self.send_request('POST', parameters)
