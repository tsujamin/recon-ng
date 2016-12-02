# module required for framework integration
from recon.core.module import BaseModule
# mixins for desired functionality
from recon.mixins.threads import ThreadingMixin
# module specific imports
import socket
import random
import string
import ssl
from datetime import datetime, timedelta


class Module(BaseModule, ThreadingMixin):

    meta = {
        'name': 'IRC User lookup',
        'author': 'Benjamin Roberts (@tsujamin)',
        'version': 'v0.0.1',
        'description': 'Looks up the usernames currently in an IRC channel',
        'comments': (
            'Note: source must be in form hostname,ip,port or hostname,port',
            '\te.g. 1.2.3.4,6667 or irc.freenode.net,6697',
        ),
        'query': 'SELECT DISTINCT ip_address, port FROM ports WHERE ip_address IS NOT NULL AND port = 6667',
        'options': (
            ('channel', None, True, 'Channel to query the names of on the IRC server'),
            ('username', None, False, 'Username to be used to connect to the IRC server'),
            ('password', None, False, 'Password to be used to connect to the IRC server'),
            ('nickname', None, False, 'Nickname to be used to connect to the IRC server'),
            ('use_ssl', False, True, 'Use SSL to connect to the IRC server?'),
        ),
    }

    def module_pre(self):
        # Initialise a list for threads to return results into
        self.table_data = []

    def module_run(self, hosts):
        self.thread(hosts)
        self.table(self.table_data, header=["server:port/channel", "users"])
        for (chan, user) in self.table_data:
            self.add_profiles(username=user, resource="irc", url="irc://{}".format(chan))

    def module_thread(self, host):
        # host is a tuple of hostname,ip_address, port
        (ip_address, port) = host
        usernames = self.query_irc_nicks(ip_address, port)

        if usernames is None:
            return

        # Add data to shared table
        chan_name = "{}:{}/{}".format(ip_address, port, self.options["channel"])
        for user in usernames:
            self.table_data.append([chan_name, user])

    def query_irc_nicks(self, host, port):
        # Default to a two second timeout
        timeout = timedelta(seconds=2)

        # Extract module options
        use_ssl = bool(self.options['use_ssl'])
        nick = self.options['nickname']
        username = self.options['username']
        password = self.options['password']
        channel = self.options['channel']

        # Generate random nick if not specified
        if nick is None:
            nick = ''.join(random.choice(string.letters) for _ in range(10))
            self.verbose('nickname missing, using {}'.format(nick))

        # If no username, use nickname
        if username is None:
            self.verbose('username missing, using nickname')
            username = nick

        # Wrap everything in a giant socket.error try/except
        try:
            # Connect to server
            self.verbose('connecting to {} on port {}.'.format(host, int(port)))
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.connect((host, int(port)))

            if use_ssl:
                self.verbose('wrapping socket with SSL.')
                s = ssl.wrap_socket(s)

            timer = datetime.now() + timeout

            # Register with IRC server
            self.verbose('registering with {}:{}.'.format(host, port))
            s.sendall('USER {} {} {} :{}\r\n'.format(username, username, username, username))
            if password is not None:
                s.sendall('PASS {}\r\n'.format(password))
            s.sendall('NICK {}\r\n'.format(nick))

            self.verbose('joining and listing names of channel {}:{}/{}.'.format(host, port, channel))
            s.sendall('JOIN {}\r\n'.format(channel))
            s.sendall('NAMES {}\r\n'.format(channel))

            r = ''
            names = []
            while datetime.now() < timer:
                r = r + s.recv(4096)
                msgs = r.split('\r\n')

                # Extract complete messages
                if len(msgs) is not 1:
                    r = msgs[-1]
                    msgs = msgs[:-1]
                else:
                    r = ''

                for msg in msgs:
                    self.debug("received message {}".format(msg))
                    # Split command up based on whether it's prefixed or not
                    if len(msg) < 1:
                        return None
                    elif msg[0] == ':':
                        command = msg.split(' ')[1]
                    else:
                        command = msg.split(' ')[0]

                    try:
                        # Responses have a response code as their 'command'
                        # Example NAMES response
                        # ':server.freenode.net 353 nick = #chan :asd asd1 asd2'
                        code = int(command)
                        if code == 366:
                            # RPL_ENDOFNAMES
                            self.verbose('no more names for {}:{}/{}, closing connection.'.format(host, port, channel))
                            s.sendall("QUIT\r\n")
                            s.close()
                            return names
                        elif code == 353:
                            # RPL_NAMREPLY
                            msg_names = msg.split(' = ')[1].split(' :')[1].split(' ')
                            self.verbose('{} name(s) received from {}:{}/{}'.format(len(msg_names), host, port, channel))
                            names = names + msg_names

                    except ValueError:
                        # Things other than command responses here
                        pass

                # Empty pending messages and update timeout
                msgs = None
                timer = datetime.now() + timeout

        except socket.error, socket.sslerror:
            pass

        # Have already returned the names in the RPL_ENDOFNAMES branch
        return None
