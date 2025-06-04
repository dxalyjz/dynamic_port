import requests
import threading
import aiohttp, asyncio
from flask import Flask, request, jsonify, Response
import json, random, time
from collections import Counter
from apscheduler.schedulers.background import BackgroundScheduler
import subprocess
from collections import Counter
import atexit
import psutil
from datetime import datetime
import logging
from pprint import pprint
import redis
import os
import sys
from flask_cors import CORS

FLASK_HOST = '127.0.0.1'
FLASK_PORT = 56972

REDIS_HOST = '127.0.0.1'
REDIS_PORT = 59528
REDIS_CHAT_HISTORY_KEY = "chat_history"
REDIS_PUBLISH_CHANNEL = "chat_channel"

with open(".redis_pass",mode="r") as f:
    REDIS_PASS = f.read().strip()

# 使用redis连接池,实现自动重连
redis_pool = redis.ConnectionPool(host=REDIS_HOST, port=REDIS_PORT,password = REDIS_PASS)
#redis_conn = redis.Redis(host='localhost', port=59528, db=0)
redis_conn = redis.Redis(connection_pool=redis_pool, db=0)
redis_conn.expire(name= REDIS_CHAT_HISTORY_KEY, time=3600 * 24) # 聊天消息将在一天后删除


app = Flask(__name__)
#CORS(app=app,methods=["GET","POST","OPTIONS"],allow_headers=['Content-Type','Authorization','X-Requested-With'],resources=r"/*",supports_credentials=True)
CORS(app=app,supports_credentials=True)


logger = logging
logger.basicConfig(level=logger.INFO)
server_info = {}  # 所有房间的信息 {server_name:{port:10000, refresh_time:600}}
ENABLE_PORT_RANDOMISE = True

MIN_VALUE_PORT = 10000
MAX_VALUE_PORT = 15000
SERVER_COUNT = 2
FIXED_PORT_LIST = [MIN_VALUE_PORT + i for i in range(50)]

REFRESH_EXTRA_TIME = 600  # 保证在查询之后的x秒内服务器端口不会变动

MAX_VALUE_ROOM_MEMBERS = 5  # 多一个位置给狗

BAN_LIST_FILE_NAME = "ban_list.txt"
BANLIST_MAGIC = "CitraRoom-BanList-1"
PID_FILE_NAME = "PID.txt"
lock = threading.Lock()


def _init():
    pid = str(os.getpid())
    with open(PID_FILE_NAME,mode="w",encoding="utf-8") as f:
        f.write(pid)
    print(pid)
    for eachIndex in range(SERVER_COUNT):
        each_server_name = f"{eachIndex + 1}"
        server_info[each_server_name] = {
            "server_port": 0,
            "server_slots": 0,
            "server_name": each_server_name,

        }

    create_rooms()


def _cleanup():
    for each_server_name in server_info:
        server_info[each_server_name]["process"].kill()


def is_room_alive(server_name):
    for each_server_name in server_info:
        if each_server_name != server_name:
            continue
        # 房间还没有被初始化,不存在房间实例
        if "process" not in server_info[server_name]:
            return False
        # 房间进程存回返回True,房间进程已经结束返回False
        return server_info[server_name]["process"].poll() is None


def get_instance_count() -> int:
    count = 0
    for each_server_name in server_info:

        if "process" not in server_info[each_server_name]:
            continue
        # if server_info[each_server_name]["process"] is None:
        #     continue
        if server_info[each_server_name]["process"].poll() is None:
            count += 1
    return count


def create_rooms():
    # 初始化变量
    new_control_api_port = ""
    new_port = ""
    server_instance = None

    # 遍历所有房间,跳过正在运行的房间,重新生成缺少的房间
    for each_server_name in server_info:
        # 跳过正在运行的房间
        if is_room_alive(each_server_name):
            continue

        available_ports = get_random_ports()
        is_room_created = False
        while len(available_ports) > 0:

            new_port = str(available_ports.pop())
            new_control_api_port = str(available_ports.pop())

            count = get_instance_count()

            if count >= SERVER_COUNT:
                logger.info("房间数量超过上限")
                return

            server_instance = run_citra_server_instance(each_server_name, new_port, new_control_api_port)

            # 二次确认端口被占用
            if server_instance.poll() is not None:
                logger.info("端口被占用,重开")
                continue
            else:
                # 房间正常生成
                is_room_created = True
                break

        if not is_room_created:
            logger.info("已经没有可用端口")
            continue

        # 初始化房间信息
        server_info[each_server_name] = {
            "refresh_time": int(time.time()) + REFRESH_EXTRA_TIME,
            "server_port": new_port,
            "process": server_instance,
            "control_api_port": new_control_api_port,
            "server_slots": 0
        }


def execute_dynamic_ports():
    #available_ports = get_random_ports()
    if not ENABLE_PORT_RANDOMISE:
        return

    with lock:
        update_server_info()
        for each_server_name in server_info:
            # 不销毁 房间不为空/房间为空但没到超时时间
            if int(server_info[each_server_name]["server_slots"]) != 0 or server_info[each_server_name][
                "refresh_time"] >= int(time.time()):
                continue
            # 销毁超时的房间
            server_info[each_server_name]["process"].kill()
            while server_info[each_server_name]["process"].poll() is None:
                time.sleep(0.1)

            create_rooms()


def port_randomizer(random_list, count):
    return random.sample(random_list, count)


def get_room_in_use() -> dict:
    _return_room_info = {}
    for each_server_name in server_info:
        # if "server_slots" not in server_info[each_server_name]:
        #     continue
        # if "server_port" not in server_info[each_server_name]:
        #     continue

        if server_info[each_server_name]["server_slots"] != 0:
            _return_room_info[each_server_name] = server_info[each_server_name]["server_port"]
    return _return_room_info


def get_room_not_in_use() -> dict:
    _return_room_info = {}
    for each_server_name in server_info:
        if server_info[each_server_name]["server_slots"] == 0:
            _return_room_info[each_server_name] = server_info[each_server_name]["server_port"]
    return _return_room_info


def get_room_port(room_name: str) -> int:
    return server_info[room_name]["server_port"]


def get_random_ports() -> list[int]:
    # 重新随机生成新的未使用端口
    system_used_ports = {p.laddr.port for p in psutil.net_connections()}
    random_range_list = list(set(range(MIN_VALUE_PORT, MAX_VALUE_PORT + 1)) - system_used_ports)

    return port_randomizer(random_range_list, len(random_range_list))


def run_citra_server_instance(server_name: str, room_port: str, control_api_port: str):
    # 开启新的房间
    data_now = datetime.now().strftime('%Y-%m-%d_%H-00-00')
    eachParam = [
        "citra-room",
        "--room-name", server_name,
        "--log-file", f"logs/ ROOM{server_name}-{data_now}.log",
        "--preferred-game", "MONSTER HUNTER 3 (tri-) G",
        "--preferred-game-id", "0004000000048100",
        "--port", room_port,
        "--max_members", str(MAX_VALUE_ROOM_MEMBERS),
        "--control-api", control_api_port,
        "--ban-list-file", "ban_list.txt",

    ]
    # server_info[server_name]["process"] = subprocess.Popen(["citra-room.exe"] + eachParam,creationflags=subprocess.CREATE_NEW_CONSOLE)
    #creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(["citra-room.exe"] + eachParam, creationflags=subprocess.CREATE_NEW_CONSOLE)


# 异步请求单个URL的函数
async def fetch_get(session, url):
    logger.debug(f"get url:{url}")
    try:
        async with session.get(url) as response:
            return await response.text()
    except(aiohttp.client_exceptions.ClientConnectorError) as e:
        logger.warning(e)
        return None


async def fetch_post(session, url, post_data):
    logger.debug(f"post url:{url},data:{post_data}")
    async with session.post(url, json=post_data) as response:
        return await response.text()


# 异步请求多个URL
async def fetch_all(urls, data={}):
    async with aiohttp.ClientSession() as session:
        if data:
            tasks = [fetch_post(session, url, data) for url in urls]
        else:
            tasks = [fetch_get(session, url) for url in urls]
        return await asyncio.gather(*tasks)


def get_servers_info():
    """
    获取所有服务器的信息
    :return:
    [{'server_description': '',
      'server_members': [],
      'server_name': '1',
      'server_port': 10478,
      'server_slots': 0
      },
     {'server_description': '',
      'server_members': [],
      'server_name': '2',
      'server_port': 13028,
      'server_slots': 0
      },
     {'server_description': '',
      'server_members': [],
      'server_name': '3',
      'server_port': 12713,
      'server_slots': 0
      }
    ]

    new:

    """

    urls = []
    for each_server_name in server_info:
        urls.append(f"http://127.0.0.1:{server_info[each_server_name]['control_api_port']}/get_room_info")

    max_retries = 5  # 最大重试次数
    retries = 0
    while retries < max_retries:
        try:

            results = asyncio.run(fetch_all(urls))
        except requests.exceptions.ConnectionError as e:
            logger.warning(e)
            retries += 1
            time.sleep(0.1)
        finally:
            break

    #
    #print(results)
    # 解析json TODO:重写从房间信息内移除狗的逻辑
    ret_json = []
    for server_name, each_room_info in enumerate(results):
        if not each_room_info:
            # ret_json.append({'server_description': '',
            #                  'server_members': [],
            #                  'server_name': f"{server_name + 1}",
            #                  'server_port': server_info[server_name + 1]["server_port"],  #保留原port
            #                  'server_slots': 0})
            logger.warning(f"缺失房间信息{server_name}")
            continue
        json_each_room_info = json.loads(each_room_info)
        if len(json_each_room_info["server_members"]):
            for index, each_player in enumerate(json_each_room_info["server_members"]):
                if (each_player["console_id_hash"] == "MyDmJ"):
                    del json_each_room_info["server_members"][index]
                    json_each_room_info["server_slots"] = int(json_each_room_info["server_slots"]) - 1
        ret_json.append(json_each_room_info)
    # TODO: 更新server_info
    return ret_json


def update_server_info():
    global server_info
    control_api_ports = []

    results = get_servers_info()

    for each_room_info in results:
        server_name = each_room_info["server_name"]
        server_port = each_room_info["server_port"]
        server_slots = each_room_info["server_slots"]

        server_info[server_name]["server_slots"] = server_slots
        server_info[server_name]["server_port"] = server_port
    return results


def get_player(player_name: str, player_ip: str):
    '''
    获取玩家信息
    :return:
    '''
    # 找到所有符合要求的玩家

    _server_info = get_servers_info()
    players = []

    # 根据ip
    if player_ip:

        for each_server in _server_info:
            for each_player in each_server["server_members"]:

                if each_player["player_ip"] == player_ip:
                    players.append({
                        "player_name": each_player["player_name"],
                        "player_game_name": each_player["player_game_name"],
                        "player_ip": each_player["player_ip"],
                        "server_name": each_server["server_name"]
                    })
        return players

    # 根据玩家名
    if player_name:

        for each_server in _server_info:
            for each_player in each_server["server_members"]:
                if each_player["player_name"] == player_name:
                    players.append({
                        "player_name": each_player["player_name"],
                        "player_game_name": each_player["player_game_name"],
                        "player_ip": each_player["player_ip"],
                        "server_name": each_server["server_name"]
                    })
    return players

    # 返回结果


def kick_player(player: dict) -> list:
    # urls = []
    # for each_server in server_info:
    #     urls.append("http://127.0.0.1:" + str(each_server["control_api_port"] + "/kick_player"))
    """

    :param player: {'player_name': '', 'player_ip': '', 'server_name': ''}
    :return:
    """
    results = []
    if not player["player_name"] and not player["player_ip"]:
        logger.warning("没有提供玩家信息")
        return results
    url = "http://127.0.0.1:" + server_info[player["server_name"]]["control_api_port"] + "/kick_player"
    if player["player_ip"]:
        results = asyncio.run(fetch_all([url], data={"player_ip": player["player_ip"]}))

    elif player["player_name"]:
        results = asyncio.run(fetch_all([url], data={"player_name": player["player_name"]}))

    return results


def load_banlist(ban_list_file):
    ban_list = []
    BANLIST_MAGIC_LINE_NUM = 0
    BANLIST_NAME_BAN_NUM = 1
    BANLIST_IP_BAN_NUM = None

    try:
        with open(ban_list_file, mode='r', encoding="utf-8") as f:
            ban_list = [line.strip() for line in f.readlines()]
    except(FileNotFoundError):
        print("BAN FILE DOES NOT EXIST")
        return []
    if len(ban_list) == 0:
        print("BAN LIST IS EMPTY")
        return []

    # 验证这个文件是否为banlist格式
    if BANLIST_MAGIC != ban_list[BANLIST_MAGIC_LINE_NUM]:
        print("NOT A BANLIST FORMAT: BANLIST_MAGIC")
        return []
    for index, each_line in enumerate(ban_list):
        if each_line == "":
            BANLIST_IP_BAN_NUM = index

    name_ban = ban_list[1:BANLIST_IP_BAN_NUM]
    if BANLIST_IP_BAN_NUM is not None:
        ip_ban = ban_list[BANLIST_IP_BAN_NUM + 1:]
    else:
        ip_ban = []

    return {
        "name_ban": name_ban,
        "ip_ban": ip_ban
    }


def save_banlist(_ban_list_dict: dict):
    ban_list_str = ""
    name_ban = ""
    ip_ban = ""
    magic = BANLIST_MAGIC + "\n"
    if "name_ban" in _ban_list_dict:
        name_ban = "\n".join(_ban_list_dict["name_ban"])
    if "ip_ban" in _ban_list_dict:
        ip_ban = "\n".join(_ban_list_dict["ip_ban"])

    # 生成banlist的字符串
    ban_list_str = magic + name_ban + "\n\n" + ip_ban

    with open(BAN_LIST_FILE_NAME, mode='w', encoding="utf-8") as f:
        f.write(ban_list_str)

def _get_return_data(return_data: dict):
    return {
        "code": 1,
        "message": "ok",
        "data": return_data
    }


@app.route('/get_servers_info_new', methods=['GET'])  # 狗
def dog_new():
    with lock:
        # 延长服务器换端口的时间
        global server_info
        for each_server_name in server_info:
            server_info[each_server_name]["refresh_time"] = int(time.time()) + REFRESH_EXTRA_TIME


        _return_server_info = []
        _return_available_rooms = []
        _return_data = {}
        results = []

        # 获取所有control_api_port

        results = update_server_info()

        for each_room_info in results:
            #_return_server_info.append(each_room_info)
            #_return_server_info.update({each_room_info["server_port"]: each_room_info})
            if "server_members" in each_room_info:
                players_names = []
                players_games = set()

                for each_player in each_room_info["server_members"]:
                    players_names.append(each_player["player_name"])
                    if each_player["player_game_name"] == "": continue
                    players_games.add(each_player["player_game_name"])

                # for each_game in each_room_info["server_members"]:
                #     if each_game["player_game_name"] == "": continue
                #     games.add(each_game["player_game_name"])
                if len(players_games) > 0:
                    server_game = str(Counter(players_games).most_common(1)[0][0])
                else:
                    server_game = "啥也没玩"
                each_room_info.update({"players_info_name": players_names})
                each_room_info.update({"players_info_game": server_game})
                _return_server_info.append(each_room_info)
                # _return_server_info[each_room_info["server_port"]].update({"players_info_name": players_names})
                # _return_server_info[each_room_info["server_port"]].update({"players_info_game": server_game})

            # 找到空房间
            if each_room_info["server_slots"] == 0:
                #_return_available_rooms.update({each_room_info["server_name"]: each_room_info["server_port"]})
                _return_available_rooms.append({"server_name":each_room_info["server_name"],"server_port":each_room_info["server_port"]})

        #_return_available_rooms = get_room_not_in_use()

        _return_data = {
            "get_servers_info": _return_server_info,
            "get_available_rooms": _return_available_rooms
        }
        _return_data = _get_return_data(_return_data)
        return jsonify(_return_data)

@app.route('/get_servers_info', methods=['GET'])  # 狗
def dog():
    with lock:
        # 延长服务器换端口的时间
        global server_info
        for each_server_name in server_info:
            server_info[each_server_name]["refresh_time"] = int(time.time()) + REFRESH_EXTRA_TIME


        _return_server_info = {}
        _return_available_rooms = {}
        _return_data = {}
        results = []

        # 获取所有control_api_port

        results = update_server_info()

        for each_room_info in results:
            _return_server_info.update({each_room_info["server_port"]: each_room_info})
            if "server_members" in each_room_info:
                players_names = []
                players_games = set()

                for each_player in each_room_info["server_members"]:
                    players_names.append(each_player["player_name"])
                    if each_player["player_game_name"] == "": continue
                    players_games.add(each_player["player_game_name"])

                # for each_game in each_room_info["server_members"]:
                #     if each_game["player_game_name"] == "": continue
                #     games.add(each_game["player_game_name"])
                if len(players_games) > 0:
                    server_game = str(Counter(players_games).most_common(1)[0][0])
                else:
                    server_game = "啥也没玩"

                _return_server_info[each_room_info["server_port"]].update({"players_info_name": players_names})
                _return_server_info[each_room_info["server_port"]].update({"players_info_game": server_game})

            # 找到空房间
            if each_room_info["server_slots"] == 0:
                _return_available_rooms.update({each_room_info["server_name"]: each_room_info["server_port"]})

        #_return_available_rooms = get_room_not_in_use()

        _return_data = {
            "get_servers_info": _return_server_info,
            "get_available_rooms": _return_available_rooms
        }

        return jsonify(_return_data)


# @app.route('/get_available_rooms', methods=['GET'])  # 狞猛碎龙
# def nmsl():
#     for each_server_name in server_info:
#         server_info[each_server_name]["refresh_time"] = int(time.time()) + REFRESH_EXTRA_TIME
#     return jsonify(get_room_not_in_use())


@app.route('/kick_player', methods=['POST'])  # 踢人
def _():
    # 获取玩家信息
    kick_request = request.get_json()

    player_name = kick_request.get("player_name")
    player_server = kick_request.get("server_name")

    player_ip = kick_request.get("player_ip")

    kick_players = get_player(player_name, player_ip)
    # print(f"kick_players:{kick_players}")

    _kick_player = {"player_name": "", "player_ip": "", "server_name": ""}
    for each_player in kick_players:
        if each_player["player_name"] == _kick_player["player_name"] and player_server == each_player[
            "server_name"]:  # 发现同房间的重名玩家
            print("有重名玩家:" + each_player["player_name"])
            _kick_player = {}
            break
        elif each_player["player_name"] == _kick_player["player_name"] and not player_server:  # 重名,没有指出房间
            print("有重名玩家:" + each_player["player_name"])
            _kick_player = {}
            break

        _kick_player["player_name"] = each_player["player_name"]
        _kick_player["player_ip"] = each_player["player_ip"]
        _kick_player["server_name"] = each_player["server_name"]

    if _kick_player["player_ip"] or _kick_player["player_name"]:
        kick_player(_kick_player)
    _return_data = _get_return_data(_kick_player)
    return jsonify(_return_data)


@app.route('/ban_player', methods=['POST'])  # 踢人
def _set_ban_player():
    ban_list_dict = {"name_ban": [], "ip_ban": []}
    req = request.get_json()

    if not req:
        return jsonify(ban_list_dict)

    player_name: str = req.get("player_name")
    player_ip: str = req.get("player_ip")
    player_server = req.get("server_name")


    ban_players = get_player(player_name, player_ip)
    # print(f"kick_players:{kick_players}")

    _ban_player = {"player_name": "", "player_ip": "", "server_name": ""}
    for each_player in ban_players:
        if each_player["player_name"] == _ban_player["player_name"] and player_server == each_player[
            "server_name"]:  # 发现同房间的重名玩家
            print("有重名玩家:" + each_player["player_name"])
            _ban_player = {}
            break
        elif each_player["player_name"] == _ban_player["player_name"] and not player_server:  # 重名,没有指出房间
            print("有重名玩家:" + each_player["player_name"])
            _ban_player = {}
            break

        _ban_player["player_name"] = each_player["player_name"]
        _ban_player["player_ip"] = each_player["player_ip"]
        _ban_player["server_name"] = each_player["server_name"]

    print(_ban_player)
    if _ban_player.get("player_ip"):
        player_ip = _ban_player["player_ip"]




    ban_list_dict = load_banlist(BAN_LIST_FILE_NAME)

    name_ban: list[str] = ban_list_dict["name_ban"]
    ip_ban: list[str] = ban_list_dict["ip_ban"]

    # if player_name and player_name not in name_ban:
    #     name_ban.append(player_name)
    if player_ip and player_ip not in ip_ban:
        ip_ban.append(player_ip)

    ban_list_dict["name_ban"] = name_ban
    ban_list_dict["ip_ban"] = ip_ban

    save_banlist(ban_list_dict)
    _return_data = _get_return_data(ban_list_dict)

    return jsonify(_return_data)


@app.route('/ban_player', methods=['GET'])  # 踢人
def _get_ban_player():
    name_ban = ""
    ip_ban = ""

    ban_list_dict = load_banlist(BAN_LIST_FILE_NAME)
    if not ban_list_dict:
        return jsonify({
            "name_ban": name_ban,
            "ip_ban": ip_ban
        })

    if "name_ban" in ban_list_dict:
        name_ban = ",".join(ban_list_dict["name_ban"])
    if "ip_ban" in ban_list_dict:
        ip_ban = ",".join(ban_list_dict["ip_ban"])

    _return_data = _get_return_data(ban_list_dict)

    # return jsonify({
    #     "name_ban": name_ban,
    #     "ip_ban": ip_ban
    # })
    return jsonify(_return_data)


@app.route('/debug_get_all_server_info', methods=['GET'])  # 狞猛碎龙
def debug_get_all_server_info():
    return jsonify(str(server_info))


@app.route('/chat_update', methods=['POST'])  # 狞猛碎龙
def __chat_update():
    # print(request.form.get("server_name"))
    # print(request.form.get("player_name"))
    # print(request.form.get("chat_message"))

    #print(request.get_json())
    #print(f"{request.form.get('player_name')}: {request.form.get('chat_message')}")
    # key 是时间戳
    #redis_conn.zadd('chat_history', {f"{request.form.get('player_name')}:{request.form.get('chat_message')}": int(time.time())})
    # 使用obj = eval(string) 将字符串变为字典
    sorted_set = f"{{'{request.form.get('player_name')}':'{request.form.get('chat_message')}','server_name':'{request.form.get('server_name')}'}}"
    # 将聊天消息写入有序集合
    redis_conn.zadd(REDIS_CHAT_HISTORY_KEY, {sorted_set : int(time.time())})

    # 发布消息到频道
    channel = "chat_channel_" + request.form.get("server_name")
    message = f"{{'{request.form.get('player_name')}':'{request.form.get('chat_message')}','server_name':'{request.form.get('server_name')}','time':'{int(time.time())}'}}"
    redis_conn.publish(channel,message)

    return jsonify(str("OK"))


@app.route('/broadcasts', methods=['POST'])  # 狞猛碎龙
def _broadcasts():
    broadcast_message = request.get_json()
    # {"broadcast_message":"公告"}
    urls = []
    for each_port in server_info:
        urls.append(f"http://127.0.0.1:{server_info[str(each_port)]['control_api_port']}/broadcast")
    #print(urls)
    asyncio.run(fetch_all(urls, broadcast_message))

    _return_data = _get_return_data({})
    return jsonify(_return_data)


@app.route('/broadcast', methods=['POST'])  # 狞猛碎龙
def _broadcast():
    broadcast_request = request.get_json()
    # {"broadcast_room":1,"broadcast_message":"公告"}

    broadcast_room: str = str(broadcast_request["broadcast_room"])
    broadcast_message = {"broadcast_message": broadcast_request["broadcast_message"]}

    url = f"http://127.0.0.1:{server_info[broadcast_room]['control_api_port']}/broadcast"
    asyncio.run(fetch_all([url], data=broadcast_message))

    _return_data = _get_return_data({})
    return jsonify(_return_data)

@app.route('/debug_close')
def debug_close():
    _cleanup()
    return "ok"

if __name__ == '__main__':
    atexit.register(_cleanup)
    _init()
    timer = BackgroundScheduler()
    timer.add_job(execute_dynamic_ports, 'interval', seconds=REFRESH_EXTRA_TIME + 4)
    timer.start()
    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=False)
