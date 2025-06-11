from nonebot import get_driver, get_bot
from .config import Config
from nonebot.rule import to_me, Rule
from nonebot.plugin import on_command, on_keyword, on_fullmatch
from nonebot.adapters import Event

import json
import asyncio
import aiohttp

async def is_available_group(event: Event) -> bool:
    # print(event.get_session_id())
    if not event.get_session_id().startswith("group"):
        return False

    return event.group_id in [819249383,222634307,315617190,753540711]
    # return event.group_id in [819249383]

async def is_available_admin_group(event: Event) -> bool:
    # print(event.get_session_id())
    if not event.get_session_id().startswith("group"):
        return False

    # return event.group_id in [819249383,222634307,315617190,753540711]
    return event.group_id in [819249383]

#normal_rule = Rule(is_available_group) & to_me()
normal_rule = Rule(is_available_group)
admin_rule = Rule(is_available_admin_group)

def jsonToString(json):
    # print(json)
    server_port = json["server_port"]
    server_name = json["server_name"]
    server_slots = json["server_slots"]
    players_info_name = str(json["players_info_name"])
    players_info_game = json["players_info_game"]
    reply = f"房间名称：{server_name}房\n端口：{server_port}\n在线人数：{server_slots}\n玩家名称：{players_info_name}\n正在玩：{players_info_game}\n\n"
    return reply

async def async_get(url):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            return await response.text()

async def async_post(url,data):
    async with aiohttp.ClientSession() as session:
        async with session.post(url,data=data) as response:
            return await response.text()

async def queryServerInfo():
    raw_res = await async_get("http://82.156.188.173:56972/get_servers_info")
    reply = ""

    if not raw_res:
        return reply

    res = json.loads(raw_res)
    if len(res["server_members"]) > 0:
        for eachInfo in res.values():
            # print(eachInfo)
            reply = jsonToString(eachInfo) + reply
    else:
        reply = "现在服务器内没有玩家"
    return reply.strip("\n")


async def get_available_port():
    ret_msg = await queryServerInfo() + "\n\n"

    ret = await async_get("http://82.156.188.173:56972/get_available_rooms")
    print(ret)
    ret = json.loads(ret)
    ret_msg = ret_msg + "以下为所有空房间端口:\n"
    for each_room_name in ret:
        ret_msg = ret_msg + f"{each_room_name}房: {ret[each_room_name]}\n"

    ret_msg = ret_msg + "服务器仅限群内使用,请不要外传！"
    return ret_msg

# room_info_at = on_command({"帮助","help"}, rule=normal_rule, priority=1, block=True)
# @room_info_at.handle()
# async def handle_help_normal(event: Event):
#     help_msg = {"在群里输入'狗',可以获取当前服务器房间信息"}
#     await room_info_at.finish(get_available_port())

admin_help = on_command("帮助", rule=admin_rule, priority=1, block=True)
@admin_help.handle()
async def handle_help_admin(event: Event):
    help_msg_admin = "封禁ip示例: 封禁ip 192.168.1.1\n封禁名称示例: 封禁名字 nana8midegou\n查看服务器封禁名单(玩家名和ip)示例: 查看封禁名单"
    await admin_help.finish(help_msg_admin)

ban_list_info = on_command("查看封禁名单", rule=admin_rule, priority=1, block=True)
@ban_list_info.handle()
async def handle_ban_list(event: Event):
    banned_info = json.loads(await async_get("http://82.156.188.173:56972/ban_player"))

    name_ban = "\n".join(banned_info["name_ban"])
    ip_ban = "\n".join(banned_info["ip_ban"])
    ret_msg = f"玩家名封禁:\n{name_ban}\n\nip封禁:\n{ip_ban}\n"
    await ban_list_info.finish(ret_msg)

ban_player_ip = on_command("封禁ip", rule=admin_rule, priority=1, block=True)
@ban_player_ip.handle()
async def handle_ban_player_ip(event: Event,args: Message = CommandArg()):
    url = "http://82.156.188.173:56972/ban_player"
    ip = args
    print(ip)
    data = {"player_ip": ip.strip()}
    await async_post(url,data)
    await ban_player_ip.finish("ok")

ban_player_name = on_command("封禁名字", rule=admin_rule, priority=1, block=True)
@ban_player_name.handle()
async def handle_ban_player_ip(event: Event,args: Message = CommandArg()):
    url = "http://82.156.188.173:56972/ban_player"
    name = args
    print(name)
    data = {"player_name": name.strip()}
    await async_post(url,data)
    await ban_player_name.finish("ok")
# room_info_at = on_command("", rule=rule, priority=1, block=False)
#
#
# @room_info_at.handle()
# async def handle_function_at(event: Event):
#     await room_info_at.finish(get_available_port())


# room_info_kw = on_keyword(
#     {"有位置", "有车位", "有车队", "有人打吗", "有人联机吗", "有人打猎", "打猎吗", "有猎人吗", "有人玩吗", "有人整吗",
#      "有玩的吗", "有坑吗", "有无猎", "有狗玩"}, rule=is_avaliable_group, priority=10, block=False)
#
#
# @room_info_kw.handle()
# async def handle_function_kw(event: Event):
#     await room_info_kw.finish(get_available_port())


room_info_certain_kw = on_fullmatch({"狗", "dog", "puppy", "pooch", "canine", "doggy", "雷door龙", "汪", "犬"},
                                    priority=10, block=True)
@room_info_certain_kw.handle()
async def handle_function_ckw(event: Event):
    await room_info_certain_kw.finish(await get_available_port())
