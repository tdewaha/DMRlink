#!/usr/bin/env python
#
# This work is licensed under the Creative Commons Attribution-ShareAlike
# 3.0 Unported License.To view a copy of this license, visit
# http://creativecommons.org/licenses/by-sa/3.0/ or send a letter to
# Creative Commons, 444 Castro Street, Suite 900, Mountain View,
# California, 94041, USA.

# This is a sample applicaiton that dumps all raw AMBE+2 voice frame data
# It is useful for things like, decoding the audio stream with a DVSI dongle, etc.

from __future__ import print_function
from twisted.internet import reactor
from binascii import b2a_hex as h
from bitstring import BitArray

import sys, socket, ConfigParser, thread, traceback
import cPickle as pickle
from dmrlink import IPSC, NETWORK, networks, logger, int_id, hex_str_3, hex_str_4, get_info, talkgroup_ids, peer_ids, PATH, get_subscriber_info, reread_subscribers
from time import time, sleep, clock
import csv
import struct
from random import randint

__author__ = 'Cortney T. Buffington, N0MJS'
__copyright__ = 'Copyright (c) 2015 Cortney T. Buffington, N0MJS and the K0USY Group'
__credits__ = 'Adam Fast, KC0YLK; Robert Garcia, N5QM'
__license__ = 'Creative Commons Attribution-ShareAlike 3.0 Unported'
__maintainer__ = 'Cort Buffington, N0MJS'
__version__ = '0.1a'
__email__ = 'n0mjs@me.com'
__status__ = 'pre-alpha'


try:
    from ipsc.ipsc_message_types import *
except ImportError:
    sys.exit('IPSC message types file not found or invalid')

try:
    from ipsc.ipsc_mask import *
except ImportError:
    sys.exit('IPSC mask values file not found or invalid')


#
# ambeIPSC class,
#
class ambeIPSC(IPSC):

    _configFile='ambe_audio.cfg'                        # Name of the config file to over-ride these default values
    _debug = False                                      # Debug output for each VOICE frame
    _outToFile = False                                  # Write each AMBE frame to a file called ambe.bin
    _outToUDP = True                                    # Send each AMBE frame to the _sock object (turn on/off DMRGateway operation)
    #_gateway = "192.168.1.184"
    _gateway = "127.0.0.1"                              # IP address of DMRGateway app
    _gateway_port = 31000                               # Port DMRGateway is listening on for AMBE frames to decode
    _remote_control_port = 31002                        # Port that ambe_audio is listening on for remote control commands
    _ambeRxPort = 31003                                 # Port to listen on for AMBE frames to transmit to all peers
    _gateway_dmr_id = 0                                 # id to use when transmitting from the gateway
    _tg_filter = [2,3,13,3174,3777215,3100,9,9998,3112]  #set this to the tg to monitor
    
    _no_tg = -99                                        # Flag (const) that defines a value for "no tg is currently active"
    _busy_slots = [0,0,0]                               # Keep track of activity on each slot.  Make sure app is polite
    _sock = -1;                                         # Socket object to send AMBE to DMRGateway
    lastPacketTimeout = 0                               # Time of last packet. Used to trigger an artifical TERM if one was not seen
    _transmitStartTime = 0                              # Used for info on transmission duration
    _start_seq = 0                                      # Used to maintain error statistics for a transmission
    _packet_count = 0                                   # Used to maintain error statistics for a transmission
    _seq = 0                                            # Transmit frame sequence number (auto-increments for each frame)
    _f = None                                           # File handle for debug AMBE binary output

    _tx_tg = hex_str_3(9998)                            # Hard code the destination TG.  This ensures traffic will not show up on DMR-MARC
    _tx_ts = 2                                          # Time Slot 2

    ###### DEBUGDEBUGDEBUG
    #_d = None
    ###### DEBUGDEBUGDEBUG
    
    def __init__(self, *args, **kwargs):
        IPSC.__init__(self, *args, **kwargs)
        self.CALL_DATA = []
        
        #
        # Define default values for operation.  These will be overridden by the .cfg file if found
        #

        self._currentTG = self._no_tg
        self.readConfigFile(self._configFile)
    
        logger.info('DMRLink ambe server')
        if self._gateway_dmr_id == 0:
            sys.exit( "Error: gatewayDmrId must be set (greater than zero)" )
        #
        # Open output sincs
        #
        if self._outToFile == True:
            self._f = open('ambe.bin', 'wb')
            logger.info('Opening output file: ambe.bin')
        if self._outToUDP == True:
            self._sock = socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
            logger.info('Send UDP frames to DMR gateway {}:{}'.format(self._gateway, self._gateway_port))
        
        ###### DEBUGDEBUGDEBUG
        #self._d = open('recordData.bin', 'wb')
        ###### DEBUGDEBUGDEBUG
    
        try:
            thread.start_new_thread( self.remote_control, (self._remote_control_port, ) )       # Listen for remote control commands
            thread.start_new_thread( self.launchUDP, (args[0], ) )                              # Package AMBE into IPSC frames and send to all peers
        except:
            traceback.print_exc()
            logger.error( "Error: unable to start thread" )
        

    # Utility function to convert bytes to string of hex values (for debug)
    def ByteToHex( self, byteStr ):
        return ''.join( [ "%02X " % ord(x) for x in byteStr ] ).strip()

    #
    # Now read the configuration file and parse out the values we need
    #
    def defaultOption( self, config, sec, opt, defaultValue ):
        try:
            _value = config.get(sec, opt).split(None)[0]            # Get the value from the named section
        except ConfigParser.NoOptionError as e:
            try:
                _value = config.get('DEFAULTS', opt).split(None)[0] # Try the global DEFAULTS section
            except ConfigParser.NoOptionError as e:
                _value = defaultValue                               # Not found anywhere, use the default value
        logger.info(opt + ' = ' + str(_value))
        return _value

    def readConfigFile(self, configFileName):
        config = ConfigParser.ConfigParser()
        try:
            config.read(configFileName)
            
            sec = self.defaultOption(config, 'DEFAULTS', 'section', 'DEFAULTS')
            self._debug = bool(self.defaultOption(config, sec,'debug', self._debug) == 'True')
            self._outToFile = bool(self.defaultOption(config, sec,'outToFile', self._outToFile) == 'True')
            self._outToUDP = bool(self.defaultOption(config, sec,'outToUDP', self._outToUDP) == 'True')

            self._gateway = self.defaultOption(config, sec,'gateway', self._gateway)
            self._gateway_port = int(self.defaultOption(config, sec,'toGatewayPort', self._gateway_port))

            self._remote_control_port = int(self.defaultOption(config, sec,'remoteControlPort', self._remote_control_port))
            self._ambeRxPort = int(self.defaultOption(config, sec,'fromGatewayPort', self._ambeRxPort))
            self._gateway_dmr_id = int(self.defaultOption(config, sec, 'gatewayDmrId', self._gateway_dmr_id))

            _tgs = self.defaultOption(config, sec,'tgFilter', str(self._tg_filter).strip('[]'))
            self._tg_filter = map(int, _tgs.split(','))

            self._tx_tg = hex_str_3(int(self.defaultOption(config, sec, 'txTg', int_id(self._tx_tg))))
            self._tx_ts = int(self.defaultOption(config, sec, 'txTs', self._tx_ts))

        except ConfigParser.NoOptionError as e:
            print('Using a default value:', e)
        except:
            traceback.print_exc()
            sys.exit('Configuration file \''+configFileName+'\' is not a valid configuration file! Exiting...')

    def rewriteFrame( self, _frame, _network, _newSlot, _newGroup, _newSouceID, _newPeerID ):
        
        _peerid         = _frame[1:5]                 # int32 peer who is sending us a packet
        _src_sub        = _frame[6:9]                 # int32 Id of source
        _burst_data_type = _frame[30]

        ########################################################################
        # re-Write the peer radio ID to that of this program
        _frame = _frame.replace(_peerid, _newPeerID)
        # re-Write the source subscriber ID to that of this program
        _frame = _frame.replace(_src_sub, _newSouceID)
        # Re-Write the destination Group ID
        _frame = _frame.replace(_frame[9:12], _newGroup)

        # Re-Write IPSC timeslot value
        _call_info = int_id(_frame[17:18])
        if _newSlot == 1:
            _call_info &= ~(1 << 5)
        elif _newSlot == 2:
            _call_info |= 1 << 5
        _call_info = chr(_call_info)
        _frame = _frame[:17] + _call_info + _frame[18:]
    
        _x = struct.pack("i", self._seq)
        _frame = _frame[:20] + _x[1] + _x[0] + _frame[22:]
        self._seq = self._seq + 1
        
        # Re-Write DMR timeslot value
        # Determine if the slot is present, so we can translate if need be
        if _burst_data_type == BURST_DATA_TYPE['SLOT1_VOICE'] or _burst_data_type == BURST_DATA_TYPE['SLOT2_VOICE']:
            # Re-Write timeslot if necessary...
            if _newSlot == 1:
                _burst_data_type = BURST_DATA_TYPE['SLOT1_VOICE']
            elif _newSlot == 2:
                _burst_data_type = BURST_DATA_TYPE['SLOT2_VOICE']
            _frame = _frame[:30] + _burst_data_type + _frame[31:]

        _frame = self.hashed_packet(NETWORK[_network]['LOCAL']['AUTH_KEY'], _frame)
        
        if (time() - self._busy_slots[_newSlot]) >= 0.10 :          # slot is not busy so it is safe to transmit
            # Send the packet to all peers in the target IPSC
            self.send_to_ipsc(_frame)
            #self.transport.write(_frame, ('192.168.1.50', 50099))
            self.transport.write(_frame, ('167.88.213.36', 50249))
        else:
            logger.info('Slot {} is busy, will not transmit packet from gateway'.format(_newSlot))

    ########################################################################

    # Read a record from the captured IPSC file looking for a payload type that matches the filter
    def readRecord(self, _file, _match_type):
        _notEOF = True
        #        _file.seek(0)
        while (_notEOF):
            _data = ""
            _bLen = _file.read(4)
            if _bLen:
                _len, = struct.unpack("i", _bLen)
                if _len > 0:
                    _data = _file.read(_len)
                    _payload_type   = _data[30]
                    if _payload_type == _match_type:
                        return _data
                else:
                    _notEOF = False
            else:
                _notEOF = False
        return _data

    # Read bytes from the socket with "timeout"  I hate this code.
    def readSock( self, _sock, len ):
        counter = 0
        while(counter < 10):
            _ambe = _sock.recv(len)
            if _ambe: break
            sleep(0.1)
            counter = counter + 1
        return _ambe
     
    # Concatenate 3 frames from the stream into a bit array and return the bytes     
    def readAmbeFrameFromUDP( self, _sock ):
        _ambeAll = BitArray()               # Start with an empty array
        for i in range(0, 3):
            _ambe = self.readSock(_sock,7)  # Read AMBE from the socket
            if _ambe:
                _ambe1 = BitArray('0x'+h(_ambe[0:49]))
                _ambeAll += _ambe1[0:50]    # Append the 49 bits to the string
        return _ambeAll.tobytes()           # Return the 49 * 3 as an array of bytes

    # Set up the socket and run the method to gather the AMBE.  Sending it to all peers
    def launchUDP(self, _network):
        s = socket.socket()                 # Create a socket object
        s.bind(('', self._ambeRxPort))      # Bind to the port

        while (1):                          # Forever!
            s.listen(5)                     # Now wait for client connection.
            _sock, addr = s.accept()        # Establish connection with client.
            self.playbackFromUDP(_sock, _network)
            _sock.close()

    # This represents a full transmission (HEAD, VOICE and TERM)
    def playbackFromUDP(self, _sock, _network):
        _delay = 0.055                                      # Yes, I know it should be 0.06, but there seems to be some latency, so this is a hack
        _src_sub = hex_str_3(self._gateway_dmr_id)          # DMR ID to sign this transmission with
        _src_peer = NETWORK[_network]['LOCAL']['RADIO_ID']  # Use this peers ID as the source repeater

        logger.info('Transmit from gateway to TG {}:'.format(int_id(self._tx_tg)) )
        try:
            
            try:
                _t = open('template.bin', 'rb')             # Open the template file.  This was recorded OTA

                _tempHead = [0] * 3                         # It appears that there 3 frames of HEAD (mostly the same)
                for i in range(0, 3):
                    _tempHead[i] = self.readRecord(_t, BURST_DATA_TYPE['VOICE_HEAD'])

                _tempVoice = [0] * 6
                for i in range(0, 6):                       # Then there are 6 frames of AMBE.  We will just use them in order
                    _tempVoice[i] = self.readRecord(_t, BURST_DATA_TYPE['SLOT2_VOICE'])
                
                _tempTerm = self.readRecord(_t, BURST_DATA_TYPE['VOICE_TERM'])
                _t.close()
            except IOError:
                logger.error('Can not open template.bin file')
                return
            logger.debug('IPSC templates loaded')
            
            _eof = False
            self._seq = randint(0,32767)                    # A transmission uses a random number to begin its sequence (16 bit)

            for i in range(0, 3):                           # Output the 3 HEAD frames to our peers
                self.rewriteFrame(_tempHead[i], _network, self._tx_ts, self._tx_tg, _src_sub, _src_peer)
                #self.group_voice(_network, _src_sub, self._tx_tg, True, '', hex_str_3(0), _tempHead[i])
                sleep(_delay)

            i = 0                                           # Initialize the VOICE template index
            while(_eof == False):
                _ambe = self.readAmbeFrameFromUDP(_sock)              # Read the 49*3 bit sample from the stream
                if _ambe:
                    i = (i + 1) % 6                         # Round robbin with the 6 VOICE templates
                    _frame = _tempVoice[i][:33] + _ambe + _tempVoice[i][52:]    # Insert the 3 49 bit AMBE frames
                    
                    self.rewriteFrame(_frame, _network, self._tx_ts, self._tx_tg, _src_sub, _src_peer)
                    #self.group_voice(_network, _src_sub, self._tx_tg, True, '', hex_str_3(0), _frame)

                    sleep(_delay)                           # Since this comes from a file we have to add delay between IPSC frames
                else:
                    _eof = True                             # There are no more AMBE frames, so terminate the loop

            self.rewriteFrame(_tempTerm, _network, self._tx_ts, self._tx_tg, _src_sub, _src_peer)
            #self.group_voice(_network, _src_sub, self._tx_tg, True, '', hex_str_3(0), _tempTerm)

        except IOError:
            logger.error('Can not transmit to peers')
        logger.info('Transmit complete')

    # Debug method used to test the AMBE code.  
    def playbackFromFile(self, _fileName):
        _r = open(_fileName, 'rb')
        _eof = False

        host = socket.gethostbyname(socket.gethostname()) # Get local machine name
        _sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        _sock.connect((host, self._ambeRxPort))
                     
        while(_eof == False):
        
            for i in range(0, 3):
                _ambe = _r.read(7)
                if _ambe:
                    _sock.send(_ambe)
                else:
                    _eof = True      
            sleep(0.055)
        logger.info('File playback complete')

    def dumpTemplate(self, _fileName):
        _file = open(_fileName, 'rb')
        _eof = False

        while(_eof == False):
            _data = ""
            _bLen = _file.read(4)
            if _bLen:
                _len, = struct.unpack("i", _bLen)
                if _len > 0:
                    _data = _file.read(_len)
                    self.dumpIPSCFrame(_data)
            else:
                _eof = True
        logger.info('File dump complete')

    #************************************************
    #     CALLBACK FUNCTIONS FOR USER PACKET TYPES
    #************************************************
    #

    def group_voice(self, _network, _src_sub, _dst_sub, _ts, _end, _peerid, _data):
        
        #self.dumpIPSCFrame(_data)
        
        # THIS FUNCTION IS NOT COMPLETE!!!!
        _payload_type = _data[30:31]
        # _ambe_frames = _data[33:52]
        _ambe_frames = BitArray('0x'+h(_data[33:52]))
        _ambe_frame1 = _ambe_frames[0:49]
        _ambe_frame2 = _ambe_frames[50:99]
        _ambe_frame3 = _ambe_frames[100:149]
        
        _tg_id = int_id(_dst_sub)
        _ts = 2 if _ts else 1
        
        self._busy_slots[_ts] = time()
        
        ###### DEBUGDEBUGDEBUG
#        if _tg_id == 2:
#            __iLen = len(_data)
#            self._d.write(struct.pack("i", __iLen))
#            self._d.write(_data)
#        else:
#            self.rewriteFrame(_data, _network, 1, 9)
        ###### DEBUGDEBUGDEBUG
       
        
        if _tg_id in self._tg_filter:    #All TGs
            _dst_sub    = get_info(int_id(_dst_sub), talkgroup_ids)
            if _payload_type == BURST_DATA_TYPE['VOICE_HEAD']:
                if self._currentTG == self._no_tg:
                    _src_sub    = get_subscriber_info(_src_sub)
                    logger.info('Voice Transmission Start on TS {} and TG {} ({}) from {}'.format(_ts, _dst_sub, _tg_id, _src_sub))
                    self._currentTG = _tg_id
                    self._transmitStartTime = time()
                    self._start_seq = int_id(_data[20:22])
                    self._packet_count = 0
                else:
                    if self._currentTG != _tg_id:
                        if time() > self.lastPacketTimeout:
                            self._currentTG = self._no_tg    #looks like we never saw an EOT from the last stream
                            logger.warning('EOT timeout')
                        else:
                            logger.warning('Transmission in progress, will not decode stream on TG {}'.format(_tg_id))
            if self._currentTG == _tg_id:
                if _payload_type == BURST_DATA_TYPE['VOICE_TERM']:
                    _source_packets = ( int_id(_data[20:22]) - self._start_seq ) - 3 # the 3 is because  the start and end are not part of the voice but counted in the RTP
                    if self._packet_count > _source_packets:
                        self._packet_count = _source_packets
                    if _source_packets > 0:
                        _lost_percentage = 100.0 - ((self._packet_count / float(_source_packets)) * 100.0)
                    else:
                        _lost_percentage = 0.0
                    logger.info('Voice Transmission End {:.2f} seconds loss rate: {:.2f}% ({}/{})'.format((time() - self._transmitStartTime), _lost_percentage, _source_packets - self._packet_count, _source_packets))
                    self._currentTG = self._no_tg
                if _payload_type == BURST_DATA_TYPE['SLOT1_VOICE']:
                    self.outputFrames(_ambe_frames, _ambe_frame1, _ambe_frame2, _ambe_frame3)
                    self._packet_count += 1
                if _payload_type == BURST_DATA_TYPE['SLOT2_VOICE']:
                    self.outputFrames(_ambe_frames, _ambe_frame1, _ambe_frame2, _ambe_frame3)
                    self._packet_count += 1
                self.lastPacketTimeout = time() + 10
    
        else:
            if _payload_type == BURST_DATA_TYPE['VOICE_HEAD']:
                _dst_sub    = get_info(int_id(_dst_sub), talkgroup_ids)
                logger.warning('Ignored Voice Transmission Start on TS {} and TG {}'.format(_ts, _dst_sub))

    def outputFrames(self, _ambe_frames, _ambe_frame1, _ambe_frame2, _ambe_frame3):
        if self._debug == True:
            logger.debug(_ambe_frames)
            logger.debug('Frame 1:', self.ByteToHex(_ambe_frame1.tobytes()))
            logger.debug('Frame 2:', self.ByteToHex(_ambe_frame2.tobytes()))
            logger.debug('Frame 3:', self.ByteToHex(_ambe_frame3.tobytes()))

        if self._outToFile == True:
            self._f.write( _ambe_frame1.tobytes() )
            self._f.write( _ambe_frame2.tobytes() )
            self._f.write( _ambe_frame3.tobytes() )

        if self._outToUDP == True:
            self._sock.sendto(_ambe_frame1.tobytes(), (self._gateway, self._gateway_port))
            self._sock.sendto(_ambe_frame2.tobytes(), (self._gateway, self._gateway_port))
            self._sock.sendto(_ambe_frame3.tobytes(), (self._gateway, self._gateway_port))

    def private_voice(self, _network, _src_sub, _dst_sub, _ts, _end, _peerid, _data):
        print('private voice')
#        __iLen = len(_data)
#        self._d.write(struct.pack("i", __iLen))
#        self._d.write(_data)

    #
    # Remote control thread
    # Use netcat to dynamically change the TGs that are forwarded to Allstar
    # echo -n "tgs=x,y,z" | nc 127.0.0.1 1235
    # echo -n "reread_subscribers" | nc 127.0.0.1 1235
    #
    def remote_control(self, port):
        s = socket.socket()         # Create a socket object
        
        s.bind(('', port))          # Bind to the port
        s.listen(5)                 # Now wait for client connection.
        logger.info('Remote control is listening on {}:{}'.format(socket.getfqdn(), port))
        
        while True:
            c, addr = s.accept()     # Establish connection with client.
            logger.info( 'Got connection from {}'.format(addr) )
            _tmp = c.recv(1024)
            _tmp = _tmp.split(None)[0] #first get rid of whitespace
            _cmd = _tmp.split('=')[0]
            logger.info('Command:"{}"'.format(_cmd))
            if _cmd:
                if _cmd == 'reread_subscribers':
                    reread_subscribers()
                elif _cmd == 'reread_config':
                    self.readConfigFile(self._configFile)
                elif _cmd == 'playbackFromFile':
                    self.playbackFromFile('ambe.bin')                
                elif _cmd == 'tgs':
                    _args = _tmp.split('=')[1]
                    self._tg_filter = map(int, _args.split(','))
                    logger.info( 'New TGs={}'.format(self._tg_filter) )
                elif _cmd == 'dump_template':
                    self.dumpTemplate('PrivateVoice.bin')
                else:
                    logger.error('Unknown command')
            c.close()                # Close the connection


    #************************************************
    #     Debug: print IPSC frame on console
    #************************************************
    def dumpIPSCFrame( self, _frame ):
        
        _packettype     = int_id(_frame[0:1])                 # int8  GROUP_VOICE, PVT_VOICE, GROUP_DATA, PVT_DATA, CALL_MON_STATUS, CALL_MON_RPT, CALL_MON_NACK, XCMP_XNL, RPT_WAKE_UP, DE_REG_REQ
        _peerid         = int_id(_frame[1:5])                 # int32 peer who is sending us a packet
        _ipsc_seq       = int_id(_frame[5:6])                 # int8  looks like a sequence number for a packet
        _src_sub        = int_id(_frame[6:9])                 # int32 Id of source
        _dst_sub        = int_id(_frame[9:12])                # int32 Id of destination
        _call_type      = int_id(_frame[12:13])               # int8 Priority Voice/Data
        _call_ctrl_info  = int_id(_frame[13:17])              # int32
        _call_info      = int_id(_frame[17:18])               # int8  Bits 6 and 7 defined as TS and END
        
        # parse out the RTP values
        _rtp_byte_1 = int_id(_frame[18:19])                 # Call Ctrl Src
        _rtp_byte_2 = int_id(_frame[19:20])                 # Type
        _rtp_seq    = int_id(_frame[20:22])                 # Call Seq No
        _rtp_tmstmp = int_id(_frame[22:26])                 # Timestamp
        _rtp_ssid   = int_id(_frame[26:30])                 # Sync Src Id
        
        _payload_type   = _frame[30]                       # int8  VOICE_HEAD, VOICE_TERM, SLOT1_VOICE, SLOT2_VOICE
        
        _ts             = bool(_call_info & TS_CALL_MSK)
        _end            = bool(_call_info & END_MSK)

        if _payload_type == BURST_DATA_TYPE['VOICE_HEAD']:
            print('HEAD:', h(_frame))
        if _payload_type == BURST_DATA_TYPE['VOICE_TERM']:
            
            _ipsc_rssi_threshold_and_parity = int_id(_frame[31])
            _ipsc_length_to_follow = int_id(_frame[32:34])
            _ipsc_rssi_status = int_id(_frame[34])
            _ipsc_slot_type_sync = int_id(_frame[35])
            _ipsc_data_size = int_id(_frame[36:38])
            _ipsc_data = _frame[38:38+(_ipsc_length_to_follow * 2)-4]
            _ipsc_full_lc_byte1 = int_id(_frame[38])
            _ipsc_full_lc_fid = int_id(_frame[39])
            _ipsc_voice_pdu_service_options = int_id(_frame[40])
            _ipsc_voice_pdu_dst = int_id(_frame[41:44])
            _ipsc_voice_pdu_src = int_id(_frame[44:47])

            print('{} {} {} {} {} {} {} {} {} {} {}'.format(_ipsc_rssi_threshold_and_parity,_ipsc_length_to_follow,_ipsc_rssi_status,_ipsc_slot_type_sync,_ipsc_data_size,h(_ipsc_data),_ipsc_full_lc_byte1,_ipsc_full_lc_fid,_ipsc_voice_pdu_service_options,_ipsc_voice_pdu_dst,_ipsc_voice_pdu_src))
            print('TERM:', h(_frame))
        if _payload_type == BURST_DATA_TYPE['SLOT1_VOICE']:
            _rtp_len        = _frame[31:32]
            _ambe           = _frame[33:52]
            print('SLOT1:', h(_frame))
        if _payload_type == BURST_DATA_TYPE['SLOT2_VOICE']:
            _rtp_len        = _frame[31:32]
            _ambe           = _frame[33:52]
            print('SLOT2:', h(_frame))
        print("pt={:02X} pid={} seq={:02X} src={} dst={} ct={:02X} uk={} ci={} rsq={}".format(_packettype, _peerid,_ipsc_seq, _src_sub,_dst_sub,_call_type,_call_ctrl_info,_call_info,_rtp_seq))

if __name__ == '__main__':
    logger.info('DMRlink \'ambe_audio.py\' (c) 2015 N0MJS & the K0USY Group - SYSTEM STARTING...')
    for ipsc_network in NETWORK:
        if NETWORK[ipsc_network]['LOCAL']['ENABLED']:
            networks[ipsc_network] = ambeIPSC(ipsc_network)
            reactor.listenUDP(NETWORK[ipsc_network]['LOCAL']['PORT'], networks[ipsc_network], interface=NETWORK[ipsc_network]['LOCAL']['IP'])
    reactor.run()
