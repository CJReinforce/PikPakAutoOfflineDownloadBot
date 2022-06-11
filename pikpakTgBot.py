import os
from telegram.ext import Updater, CallbackContext, CommandHandler, MessageHandler, Filters
import logging
from telegram import Update
import re
from time import sleep, time
import requests
import json
import threading
from config import *

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)

# 全局变量
SCHEMA = 'https' if ARIA2_HTTPS else 'http'
PIKPAK_API_URL = "https://api-drive.mypikpak.com"
PIKPAK_USER_URL = "https://user.mypikpak.com"

# 记录登陆账号的headers，调用api用
# pikpak_headers = None
pikpak_headers = [None] * len(USER)
# 命令运行标志，防止下载与删除命令同时运行
running = False
# 记录待下载的磁力链接
mag_urls = []
# PTB所需
if TG_API_URL[-1] == '/':
    updater = Updater(token=TOKEN, base_url=f"{TG_API_URL}bot", base_file_url=f"{TG_API_URL}file/bot")
else:
    updater = Updater(token=TOKEN, base_url=f"{TG_API_URL}/bot", base_file_url=f"{TG_API_URL}/file/bot")

dispatcher = updater.dispatcher


def start(update: Update, context: CallbackContext):
    context.bot.send_message(chat_id=update.effective_chat.id, text="【命令简介】\n/pikpak\t自动离线+aria2下载+释放网盘空间\n/clean\t清空账号网盘空间（"
                                                                    "请慎用！清空文件无法找回！！！）")


# 账号密码登录
def login(index):
    global pikpak_headers

    # 登录所需所有信息
    login_admin = USER[index]
    login_password = PASSWORD[index]
    login_url = f"{PIKPAK_USER_URL}/v1/auth/signin?client_id=YNxT9w7GMdWvEOKa"
    login_data = {"captcha_token": "",
                  "client_id": "YNxT9w7GMdWvEOKa",
                  "client_secret": "dbw2OtmVEeuUvIptb1Coyg",
                  "password": login_password, "username": login_admin}
    headers = {
        'User-Agent': 'protocolversion/200 clientid/YNxT9w7GMdWvEOKa action_type/ networktype/WIFI sessionid/ '
                      'devicesign/div101.073163586e9858ede866bcc9171ae3dcd067a68cbbee55455ab0b6096ea846a0 sdkversion/1.0.1.101300 '
                      'datetime/1630669401815 appname/android-com.pikcloud.pikpak session_origin/ grant_type/ clientip/ devicemodel/LG '
                      'V30 accesstype/ clientversion/ deviceid/073163586e9858ede866bcc9171ae3dc providername/NONE refresh_token/ '
                      'usrno/null appid/ devicename/Lge_Lg V30 cmd/login osversion/9 platformversion/10 accessmode/',
        'Content-Type': 'application/json; charset=utf-8',
        'Host': 'user.mypikpak.com',
    }
    # 请求登录api
    info = requests.post(url=login_url, json=login_data, headers=headers, timeout=5).json()

    # 获得调用其他api所需的headers
    headers['Authorization'] = f"Bearer {info['access_token']}"
    headers['Host'] = 'api-drive.mypikpak.com'
    pikpak_headers[index] = headers.copy()  # 拷贝

    logging.info(f"账号{USER[index]}登陆成功！")


# 获得headers，用于请求api
def get_headers(index):
    if not pikpak_headers[index]:  # headers为空则先登录
        login(index)
    return pikpak_headers[index]


# 离线下载磁力
def magnet_upload(file_url, index):
    # 请求离线下载所需数据
    login_headers = get_headers(index)
    torrent_url = f"{PIKPAK_API_URL}/drive/v1/files"
    torrent_data = {
        "kind": "drive#file",
        "name": "",
        "upload_type": "UPLOAD_TYPE_URL",
        "url": {
            "url": file_url
        },
        "folder_type": "DOWNLOAD"
    }
    # 请求离线下载
    torrent_result = requests.post(url=torrent_url, headers=login_headers, json=torrent_data, timeout=5).json()

    # 处理请求异常
    if "error" in torrent_result:
        if torrent_result['error_code'] == 16:
            logging.info(f"账号{USER[index]}登录过期，正在重新登录")
            login(index)  # 重新登录该账号
            login_headers = get_headers(index)
            torrent_result = requests.post(url=torrent_url, headers=login_headers, json=torrent_data, timeout=5).json()

        else:
            # 可以考虑加入删除离线失败任务的逻辑
            logging.error(f"账号{USER[index]}提交离线下载任务失败，错误信息：{torrent_result['error_description']}")
            return None, None

    # 输出日志
    file_url_part = re.search(r'^(magnet:\?).*(xt=.+?)(&|$)', file_url)
    if file_url_part:
        file_url_simple = ''.join(file_url_part.groups()[:-1])
        logging.info(f"账号{USER[index]}添加离线磁力任务:{file_url_simple}")
    else:
        logging.info(f"账号{USER[index]}添加离线磁力任务:{file_url}")

    # 返回离线任务id、下载文件名
    return torrent_result['task']['id'], torrent_result['task']['name']


# 获取所有离线任务
def get_offline_list(index):
    # 准备信息
    login_headers = get_headers(index)
    offline_list_url = f"{PIKPAK_API_URL}/drive/v1/tasks?type=offline&page_token=&thumbnail_size=SIZE_LARGE&filters=%7B%7D&with" \
                       f"=reference_resource "
    # 发送请求
    offline_list_info = requests.get(url=offline_list_url, headers=login_headers, timeout=5).json()
    # 处理错误
    if "error" in offline_list_info:
        if offline_list_info['error_code'] == 16:
            logging.info(f"账号{USER[index]}登录过期，正在重新登录")
            login(index)
            login_headers = get_headers(index)
            offline_list_info = requests.get(url=offline_list_url, headers=login_headers, timeout=5).json()
        else:
            logging.error(f"账号{USER[index]}获取离线任务失败，错误信息：{offline_list_info['error_description']}")
            return []

    return offline_list_info['tasks']


# 获取下载信息
def get_download_url(file_id, index):
    try:
        # 准备信息
        login_headers = get_headers(index)
        download_url = f"{PIKPAK_API_URL}/drive/v1/files/{file_id}?magic=2021&thumbnail_size=SIZE_LARGE"
        # 发送请求
        download_info = requests.get(url=download_url, headers=login_headers, timeout=5).json()
        logging.debug('返回文件信息包括：\n' + str(download_info))

        # 处理错误
        if "error" in download_info:
            if download_info['error_code'] == 16:
                logging.info(f"账号{USER[index]}登录过期，正在重新登录")
                login(index)
                login_headers = get_headers(index)
                download_info = requests.get(url=download_url, headers=login_headers, timeout=5).json()
            else:
                logging.error(f"账号{USER[index]}获取文件下载信息失败，错误信息：{download_info['error_description']}")
                return "", ""

        # 返回文件名、文件下载直链
        return download_info['name'], download_info['web_content_link']

    except Exception as e:
        logging.error(f'账号{USER[index]}获取文件下载信息失败：{e}')
        return "", ""


# 获取文件夹下所有id
def get_list(folder_id, index):
    try:
        file_list = []
        # 准备信息
        login_headers = get_headers(index)
        list_url = f"{PIKPAK_API_URL}/drive/v1/files?parent_id={folder_id}&thumbnail_size=SIZE_LARGE" + "&filters={\"trashed\":{%22eq%22:false}}"
        # 发送请求
        list_result = requests.get(url=list_url, headers=login_headers, timeout=5).json()
        # 处理错误
        if "error" in list_result:
            if list_result['error_code'] == 16:
                logging.info(f"账号{USER[index]}登录过期，正在重新登录")
                login(index)
                login_headers = get_headers(index)
                list_result = requests.get(url=list_url, headers=login_headers, timeout=5).json()
            else:
                logging.error(f"账号{USER[index]}获取文件夹下文件id失败，错误信息：{list_result['error_description']}")
                return file_list

        file_list += list_result['files']

        # 获取下一页
        while list_result['next_page_token'] != "":
            list_url = f"{PIKPAK_API_URL}/drive/v1/files?parent_id={folder_id}&page_token=" + list_result['next_page_token'] + \
                       "&thumbnail_size=SIZE_LARGE" + "&filters={\"trashed\":{%22eq%22:false}} "

            list_result = requests.get(url=list_url, headers=login_headers, timeout=5).json()

            file_list += list_result['files']

        # logging.info(file_list)
        return file_list

    except Exception as e:
        logging.error(f"账号{USER[index]}获取文件夹下文件id失败:{e}")
        return []


# 获取文件夹及其子目录下所有文件id
def get_folder_all_file(folder_id, path, index):
    # 获取该文件夹下所有id
    folder_list = get_list(folder_id, index)
    # 逐个判断每个id
    for a in folder_list:
        # 如果是文件
        if a["kind"] == "drive#file":
            down_name, down_url = get_download_url(a["id"], index)
            if down_name == "":
                continue
            yield down_name, down_url, a['id'], path  # 文件名、下载直链、文件id、文件路径
        # 如果是根目录且文件夹是My Pack，则不更新path
        elif a['name'] == 'My Pack' and folder_id == '':
            yield from get_folder_all_file(a["id"], path, index)
        # 其他文件夹
        else:
            new_path = path + a['name'] + "/"
            yield from get_folder_all_file(a["id"], new_path, index)


# 获取根目录文件夹下所有文件、文件夹id，清空网盘时用
def get_folder_all(index):
    # 获取根目录文件夹下所有id
    folder_list = get_list('', index)
    # 逐个判断每个id
    for a in folder_list:
        # 是文件则直接返回id
        if a["kind"] == "drive#file":
            yield a['id']
        # My Pack文件夹则获取其下所有id
        elif a["name"] == 'My Pack':
            for b in get_list(a['id'], index):
                yield b['id']
        # 其他文件夹也直接返回id
        else:
            yield a['id']


# 删除文件夹、文件
def delete_files(file_id, index):
    # 准备数据
    login_headers = get_headers(index)
    delete_files_url = f"{PIKPAK_API_URL}/drive/v1/files:batchTrash"
    if type(file_id) == list:  # 可以删除多个id
        delete_files_data = {"ids": file_id}
    else:
        delete_files_data = {"ids": [file_id]}
    # 发送请求
    delete_files_result = requests.post(url=delete_files_url, headers=login_headers, json=delete_files_data, timeout=5).json()
    # 处理错误
    if "error" in delete_files_result:
        if delete_files_result['error_code'] == 16:
            logging.info(f"账号{USER[index]}登录过期，正在重新登录")
            login(index)
            login_headers = get_headers(index)
            delete_files_result = requests.post(url=delete_files_url, headers=login_headers, json=delete_files_data, timeout=5).json()

        else:
            logging.error(f"账号{USER[index]}删除网盘文件失败，错误信息：{delete_files_result['error_description']}")
            return False

    return True


# 删除回收站id
def delete_trash(file_id, index):
    # 准备信息
    login_headers = get_headers(index)
    delete_files_url = f"{PIKPAK_API_URL}/drive/v1/files:batchDelete"
    if type(file_id) == list:  # 可以删除多个id
        delete_files_data = {"ids": file_id}
    else:
        delete_files_data = {"ids": [file_id]}
    # 发送请求
    delete_files_result = requests.post(url=delete_files_url, headers=login_headers, json=delete_files_data, timeout=5).json()
    # 处理错误
    if "error" in delete_files_result:
        if delete_files_result['error_code'] == 16:
            logging.info(f"账号{USER[index]}登录过期，正在重新登录")
            login(index)
            login_headers = get_headers(index)
            delete_files_result = requests.post(url=delete_files_url, headers=login_headers, json=delete_files_data, timeout=5).json()
        else:
            logging.error(f"账号{USER[index]}删除回收站文件失败，错误信息：{delete_files_result['error_description']}")
            return False

    return True


# /pikpak命令主程序
def main(update: Update, context: CallbackContext):
    global running, mag_urls
    running = True  # 保证下载命令和删除命令不同时进行

    for mag_url in mag_urls:
        try:  # 捕捉所有的请求超时异常
            for account_index in range(len(USER)):
                # 磁链的简化表示，不保证兼容所有磁链，仅为显示信息时比较简介，不影响任何实际功能
                mag_url_part = re.search(r'^(magnet:\?).*(xt=.+?)(&|$)', mag_url)
                mag_url_simple = ''.join(mag_url_part.groups()[:-1])

                # 登录
                login(account_index)  # 指定用哪个账户登录
                # 离线下载并获取任务id和文件名
                mag_id, mag_name = magnet_upload(mag_url, account_index)

                if not mag_id:  # 如果添加离线失败，那就试试下一个账号
                    if account_index == len(USER) - 1:  # 最后一个账号仍然无法离线下载
                        print_info = f'磁链{mag_url_simple}所有账号均离线下载失败！可能是所有账号免费离线次数用尽，或者文件大小超过网盘剩余容量！'
                        context.bot.send_message(chat_id=update.effective_chat.id, text=print_info)
                        logging.warning(print_info)
                    continue

                # 查询是否离线完成
                done = False  # 是否完成标志
                zero_process = False  # 是否卡在零进度标志
                logging.info('5s后将检查离线下载进度...')
                sleep(5)  # 等待5秒，一般是秒离线，可以保证大多数情况下直接就完成了离线下载
                offline_start = time()  # 离线开始时间
                while (not done) and (time() - offline_start < 60):  # 要么完成要么超时
                    temp = get_offline_list(account_index)  # 获取离线列表
                    find = False  # 离线列表中找到了任务id的标志
                    for each_down in temp:
                        if each_down['id'] == mag_id:  # 匹配上任务id就是找到了
                            find = True
                            if each_down['progress'] == 100 and each_down['message'] == 'Saved':  # 查看完成了吗
                                done = True
                                file_id = each_down['file_id']
                                # 输出信息
                                print_info = f'账号{USER[account_index]}离线下载磁链已完成!：\n{mag_url_simple}\n文件名称：{mag_name}'
                                context.bot.send_message(chat_id=update.effective_chat.id, text=print_info)
                                logging.info(print_info)
                            elif each_down['progress'] == 100:  # 可能存在错误但还是允许推送aria2下载了
                                done = True
                                file_id = each_down['file_id']
                                # 输出信息
                                print_info = f'账号{USER[account_index]}离线下载磁链已完成!:\n{mag_url_simple}\n但含有错误信息：' \
                                             f'{each_down["message"].strip()}！\n文件名称：{mag_name}'
                                context.bot.send_message(chat_id=update.effective_chat.id, text=print_info)
                                logging.warning(print_info)
                            elif each_down['progress'] == 0:  # 共20s都卡在进度0，则认为网盘无法离线此文件
                                if zero_process:  # 如果上一次查询就是零进度了，这次又是零进度
                                    find = False
                                    print_info = f'账号{USER[account_index]}离线{mag_url_simple}进度卡在0%，将取消离线任务'
                                    context.bot.send_message(chat_id=update.effective_chat.id, text=print_info)
                                    logging.warning(print_info)
                                else:
                                    zero_process = True
                                    logging.warning(f'查询离线进度为0%，将在15s后再次查看...')
                                    sleep(15)
                            else:
                                logging.info(f'账号{USER[account_index]}离线下载{mag_url_simple}还未完成，进度{each_down["progress"]}...')
                                sleep(5)
                            # 只要找到了就可以退出查找循环
                            break
                    # 非正常退出查询离线完成方式
                    if not find:  # 一轮下来没找到可能是删除或者添加失败等等异常
                        print_info = f'账号{USER[account_index]}离线下载{mag_url_simple}的任务被取消！'
                        context.bot.send_message(chat_id=update.effective_chat.id, text=print_info)
                        logging.warning(print_info)
                        break

                # 查询账号是否完成离线
                if (find and done) or (not find and not done):  # 前者找到离线任务并且完成了，后者是要么手动取消了要么卡在进度0
                    break
                elif find and not done:
                    print_info = f'账号{USER[account_index]}离线下载{mag_url_simple}的任务超时！已取消该任务！'
                    context.bot.send_message(chat_id=update.effective_chat.id, text=print_info)
                    logging.warning(print_info)
                    break
                else:  # 其他情况都换个号再试
                    continue

            # 如果找到了任务并且任务已完成，则开始从网盘下载到本地
            if mag_id and find and done:  # 判断mag_id是否为空防止所有号次数用尽的情况
                gid = {}  # 记录每个下载任务的gid，{gid:[文件名,file_id,下载直链]}
                # 偶尔会出现aria2下载失败，报ssl i/o error错误，试试加上headers
                download_headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.9; rv:50.0) Gecko/20100101 Firefox/50.0'}

                down_name, down_url = get_download_url(file_id, account_index)
                # 获取到文件夹
                if down_url == "":
                    logging.info(f"识别为文件夹:{down_name}，准备提取出每个文件并下载")
                    for name, url, down_file_id, path in get_folder_all_file(file_id, f"{down_name}/", account_index):
                        jsonreq = json.dumps({'jsonrpc': '2.0', 'id': 'qwer', 'method': 'aria2.addUri',
                                              'params': [f"token:{ARIA2_SECRET}", [url],
                                                         {"dir": ARIA2_DOWNLOAD_PATH + '/' + path, "out": f"{name}",
                                                          "header": download_headers}]})

                        push_flag = False  # 成功推送aria2下载标志
                        # 文件夹的推送下载是网络请求密集地之一，每个链接将尝试5次
                        for tries in range(5):
                            try:
                                response = requests.post(f'{SCHEMA}://{ARIA2_HOST}:{ARIA2_PORT}/jsonrpc', data=jsonreq, timeout=5).json()
                                push_flag = True
                                break
                            except requests.exceptions.ReadTimeout:
                                logging.warning(f'{name}第{tries + 1}(/5)次推送下载超时，将重试！')
                                continue
                            except json.JSONDecodeError:
                                logging.warning(f'{name}第{tries + 1}(/5)次推送下载出错，可能是frp故障，将重试！')
                                sleep(5)  # frp问题就休息一会
                                continue
                        if not push_flag:  # 5次都推送下载失败，让用户手动下载该文件，并且要检查网络！
                            print_info = f'{name}推送aria2下载失败！该文件直链如下，请手动下载：\n{url}'
                            context.bot.send_message(chat_id=update.effective_chat.id, text=print_info)
                            logging.error(print_info)
                            continue  # 这个文件让用户手动下载，程序处理下一个文件

                        gid[response['result']] = [f'{name}', down_file_id, url]
                        # context.bot.send_message(chat_id=update.effective_chat.id, text=f'{name}推送aria2下载')  # 注释掉防止发送消息过多
                        logging.info(f'{path}{name}推送aria2下载')

                    # 文件夹所有文件都推送完后再发送信息，避免消息过多
                    print_info = f'{down_name}文件夹下所有文件已推送aria2下载，请耐心等待...'
                    context.bot.send_message(chat_id=update.effective_chat.id, text=print_info)
                    logging.info(print_info)

                # 否则是单个文件，只推送一次，不用太担心网络请求出错
                else:
                    logging.info('识别为单文件，将直接推送aria2下载')
                    jsonreq = json.dumps({'jsonrpc': '2.0', 'id': 'qwer', 'method': 'aria2.addUri',
                                          'params': [f"token:{ARIA2_SECRET}", [down_url],
                                                     {"dir": ARIA2_DOWNLOAD_PATH, "out": down_name, "header": download_headers}]})
                    response = requests.post(f'{SCHEMA}://{ARIA2_HOST}:{ARIA2_PORT}/jsonrpc', data=jsonreq, timeout=5).json()
                    gid[response['result']] = [down_name, file_id, down_url]
                    context.bot.send_message(chat_id=update.effective_chat.id, text=f'{down_name}推送aria2下载')
                    logging.info(f'{down_name}已推送aria2下载，请耐心等待...')

                logging.info('睡眠30s，之后将开始查询下载进度...')
                # pikpak单文件限速6MB/s
                sleep(30)
                # 查询每个gid是否完成
                download_done = False
                complete_file_id = []  # 记录aria2下载成功的文件id
                failed_gid = {}  # 记录下载失败的gid
                while not download_done:
                    temp_gid = gid.copy()  # 下面的操作仅对temp_gid进行，别污染gid
                    for each_gid in gid.keys():
                        # 这里是网络请求最密集的地方，一次查询失败跳过即可
                        try:
                            jsonreq = json.dumps({'jsonrpc': '2.0', 'id': 'qwer', 'method': 'aria2.tellStatus',
                                                  'params': [f"token:{ARIA2_SECRET}", each_gid, ["gid", "status", "errorMessage", "dir"]]})
                            response = requests.post(f'{SCHEMA}://{ARIA2_HOST}:{ARIA2_PORT}/jsonrpc', data=jsonreq, timeout=5).json()
                        except requests.exceptions.ReadTimeout:  # 超时就查询下一个gid，跳过一个无所谓的
                            logging.warning(f'查询GID{each_gid}时网络请求超时，将跳过此次查询！')
                            continue
                        except json.JSONDecodeError:
                            logging.warning(f'查询GID{each_gid}时返回结果错误，可能是frp故障，将跳过此次查询！')
                            sleep(5)  # frp的问题就休息一会
                            continue

                        try:  # 检查任务状态
                            status = response['result']['status']
                            if status == 'complete':  # 完成了删除对应的gid并记录成功下载
                                temp_gid.pop(each_gid)  # 不再查询此gid
                                complete_file_id.append(gid[each_gid][1])  # 将它记为已完成gid
                            elif status == 'error':  # 如果aria2下载产生error
                                error_message = response["result"]["errorMessage"]  # 识别错误信息
                                # 如果是这两种错误信息，可尝试重新推送aria2下载来解决
                                if error_message in ['No URI available.', 'SSL/TLS handshake failure: SSL I/O error']:
                                    # 再次推送aria2下载
                                    retry_down_name, retry_the_url = get_download_url(gid[each_gid][1], account_index)
                                    # 这只可能是文件，不会是文件夹
                                    jsonreq = json.dumps({'jsonrpc': '2.0', 'id': 'qwer', 'method': 'aria2.addUri',
                                                          'params': [f"token:{ARIA2_SECRET}", [retry_the_url],
                                                                     {"dir": response["result"]["dir"], "out": retry_down_name,
                                                                      "header": download_headers}]})
                                    # 当失败文件较多时，这里也是网络请求密集地
                                    repush_flag = False
                                    for tries in range(5):
                                        try:
                                            response = requests.post(f'{SCHEMA}://{ARIA2_HOST}:{ARIA2_PORT}/jsonrpc',
                                                                     data=jsonreq, timeout=5).json()
                                            repush_flag = True
                                            break
                                        except requests.exceptions.ReadTimeout:
                                            logging.warning(f'{retry_down_name}下载异常后重新推送第{tries + 1}(/5)次网络请求超时！将重试')
                                            continue
                                        except json.JSONDecodeError:
                                            logging.warning(f'{retry_down_name}下载异常后重新推送第{tries + 1}(/5)次返回结果错误，可能是frp故障！将重试！')
                                            sleep(5)  # frp的问题就休息一会
                                            continue
                                    if not repush_flag:  # ?次重新推送失败，则认为此文件下载失败，让用户手动下载
                                        print_info = f'{retry_down_name}下载异常后重新推送失败！该文件下载直链如下，请手动下载：\n{retry_the_url}'
                                        context.bot.send_message(chat_id=update.effective_chat.id, text=print_info)
                                        logging.error(print_info)
                                        failed_gid[each_gid] = temp_gid.pop(each_gid)  # 5次都不成功，别管这个任务了，放弃吧没救了
                                        continue  # 程序将查询下一个gid

                                    # 重新记录gid
                                    temp_gid[response['result']] = [retry_down_name, gid[each_gid][1], retry_the_url]
                                    # 删除旧的gid
                                    temp_gid.pop(each_gid)
                                    # 消息提示
                                    # context.bot.send_message(chat_id=update.effective_chat.id,
                                    #                          text=f'aria2下载{gid[each_gid][0]}出错！错误信息：'
                                    #                               f'{error_message}\n此文件已重新推送aria2下载！')
                                    logging.warning(f'aria2下载{gid[each_gid][0]}出错！错误信息：{error_message}\t此文件已重新推送aria2下载！')
                                # 其他错误信息暂未遇到，先跳过处理
                                else:
                                    print_info = f'aria2下载{gid[each_gid][0]}出错！错误信息：{error_message}\t该文件下载直链如下，' \
                                                 f'请手动下载并反馈bug：\n{gid[each_gid][2]}'
                                    context.bot.send_message(chat_id=update.effective_chat.id, text=print_info)
                                    logging.warning(print_info)
                                    failed_gid[each_gid] = temp_gid.pop(each_gid)  # 认为该任务失败

                        except KeyError:  # 此时任务可能已被手动删除
                            context.bot.send_message(chat_id=update.effective_chat.id, text=f'aria2下载{gid[each_gid][0]}任务被删除！')
                            logging.warning(f'aria2下载{gid[each_gid][0]}任务被删除！')
                            failed_gid[each_gid] = temp_gid.pop(each_gid)  # 认为该任务失败

                    # 判断完所有下载任务情况
                    gid = temp_gid
                    if len(gid) == 0:
                        download_done = True
                        print_info = f'aria2下载{down_name}已完成，共{len(complete_file_id) + len(failed_gid)}个文件，' \
                                     f'其中{len(complete_file_id)}个成功，{len(failed_gid)}个失败'
                        # 输出下载失败的文件信息
                        if len(failed_gid):
                            print_info += '，下载失败文件为：\n'
                            for values in failed_gid.values():
                                print_info += values[0] + '\n'

                            # 存在失败文件则只释放成功文件的网盘空间
                            delete_files(complete_file_id, account_index)
                            logging.info(f'已删除{down_name}中下载成功的网盘文件')
                            delete_trash(complete_file_id, account_index)
                            logging.info(f'已删除{down_name}中下载成功的回收站文件')
                            print_info += '下载成功的网盘文件已删除\n'

                            # /download命令仅打算临时解决问题，当/pikpak命令足够健壮后将弃用/download命令
                            print_info += f'对于下载失败的文件可使用命令：\n`/clean {USER[account_index]}`清空此账号下所有文件\n或者使用临时命令：' \
                                          f'\n~~`/download {USER[account_index]}`重试下载此账号下所有文件~~'
                        else:
                            # 没有失败文件，则直接删除该文件根目录
                            delete_files(file_id, account_index)
                            logging.info(f'已删除{down_name}网盘文件')
                            delete_trash(file_id, account_index)
                            logging.info(f'已删除{down_name}回收站文件')
                            print_info += '\n该文件的网盘空间已释放'

                        # 发送下载结果统计信息
                        context.bot.send_message(chat_id=update.effective_chat.id, text=print_info, parse_mode='Markdown')
                        logging.info(print_info)
                    else:
                        logging.info(f'aria2下载{down_name}还未完成，睡眠20s后进行下一次查询...')
                        sleep(20)

        except requests.exceptions.ReadTimeout:
            print_info = f'下载磁链{mag_url_simple}时网络请求超时！可稍后重试`/pikpak {mag_url_simple}`'
            context.bot.send_message(chat_id=update.effective_chat.id, text=print_info, parse_mode='Markdown')
            logging.error(print_info)
            continue

    running = False
    mag_urls = []


def pikpak(update: Update, context: CallbackContext):
    global mag_urls

    temp = context.args  # 获取命令参数

    if len(temp) == 0:  # 如果仅为/pikpak命令，没有附带参数则返回帮助信息
        context.bot.send_message(chat_id=update.effective_chat.id, text='【用法】\n/pikpak magnet1 [magnet2] [...]')
    else:
        print_info = '下载队列添加离线磁力任务：\n'  # 将要输出的信息
        for each_text in temp:  # 逐个判断每个参数是否为磁力链接，并提取出
            if each_text.startswith('magnet:?'):  # 只要以magnet:?开头则认为是磁力链接
                mag_urls.append(each_text)

            # 显示信息为了简洁，仅提取磁链中xt参数部分
            mag_url_part = re.search(r'^(magnet:\?).*(xt=.+?)(&|$)', each_text)
            if mag_url_part:  # 正则匹配上，则输出信息
                print_info += ''.join(mag_url_part.groups()[:-1])
            else:  # 否则输出未识别信息
                print_info += f'未识别链接：{each_text}'
            print_info += '\n\n'

        context.bot.send_message(chat_id=update.effective_chat.id, text=print_info.rstrip())
        logging.info(print_info.rstrip())

        if not running:
            t = threading.Thread(target=main, name='DownloadThread', args=[update, context])  # 开线程跑下载，防止阻塞
            t.start()


def clean(update: Update, context: CallbackContext):
    argv = context.args  # 获取命令参数

    if not running:
        # 清空网盘应该阻塞住进程，防止一边下一边删

        if len(argv) == 0:  # 直接/clean则显示帮助
            context.bot.send_message(chat_id=update.effective_chat.id, text='【用法】\n`/clean all`\t清空所有账号网盘\n/clean 账号1 [账号2] [...]'
                                                                            '\t清空指定账号网盘', parse_mode='Markdown')
        elif argv[0] in ['a', 'all']:
            for temp_account_index in range(len(USER)):
                login(temp_account_index)
                all_file_id = list(get_folder_all(temp_account_index))
                # 如果没东西可删，那就下一个账号
                if len(all_file_id) == 0:
                    context.bot.send_message(chat_id=update.effective_chat.id, text=f'账号{USER[temp_account_index]}网盘无需清空')
                    logging.info(f'账号{USER[temp_account_index]}网盘无需清空')
                    continue
                delete_files(all_file_id, temp_account_index)
                delete_trash(all_file_id, temp_account_index)
                context.bot.send_message(chat_id=update.effective_chat.id, text=f'账号{USER[temp_account_index]}网盘已清空')
                logging.info(f'账号{USER[temp_account_index]}网盘已清空')
        else:
            for each_account in argv:  # 输入参数是账户名称
                try:
                    temp_account_index = USER.index(each_account)
                except ValueError:
                    context.bot.send_message(chat_id=update.effective_chat.id, text=f'账号{each_account}不存在！')
                    continue
                login(temp_account_index)
                all_file_id = list(get_folder_all(temp_account_index))
                # logging.info(all_file_id)
                # 如果没东西可删，那就下一个账号
                if len(all_file_id) == 0:
                    context.bot.send_message(chat_id=update.effective_chat.id, text=f'账号{each_account}网盘无需清空')
                    logging.info(f'账号{each_account}网盘无需清空')
                    continue
                delete_files(all_file_id, temp_account_index)
                delete_trash(all_file_id, temp_account_index)
                context.bot.send_message(chat_id=update.effective_chat.id, text=f'账号{each_account}网盘已清空')
                logging.info(f'账号{each_account}网盘已清空')

    else:
        context.bot.send_message(chat_id=update.effective_chat.id, text='有命令正在运行，为避免冲突请稍后再试~')


# 打印账号和是否vip
def print_user_vip():
    print_info = '账号                                   vip\n'  # \
    # ' -----------------------------        --------- \n'
    for temp_index, each_user in enumerate(USER):
        flag = get_my_vip(temp_index)
        if flag == 0:
            flag = '√'
        elif flag == 1:
            flag = '×'
        elif flag == 2:
            flag = '?'
        else:
            flag = '××'  # 登陆失败，检查账号密码
        print_info += f' `{each_user}`      {flag}   \n'
    return print_info.rstrip()


# 仅打印账号
def print_user():
    print_info = "账号：\n"
    for each_user in USER:
        print_info += f'`{each_user}`\n'
    return print_info.rstrip()


# 写config.py文件
def record_config():
    # 写入同目录下的config.py文件
    with open(os.path.abspath(os.path.dirname(__file__)) + '/config.py', 'w') as f:
        f.write(f'TOKEN = "{TOKEN}"\nUSER = {USER}\nPASSWORD = {PASSWORD}\nARIA2_HTTPS = {ARIA2_HTTPS}\nARIA2_HOST = "{ARIA2_HOST}"\n'
                f'ARIA2_PORT = "{ARIA2_PORT}"\nARIA2_SECRET = "{ARIA2_SECRET}"\nARIA2_DOWNLOAD_PATH = "{ARIA2_DOWNLOAD_PATH}"\n'
                f'TG_API_URL = "{TG_API_URL}"')
    logging.info('已更新config.py文件')


# 判断是否为vip
def get_my_vip(index):
    try:
        login_headers = get_headers(index)

        me_url = f"{PIKPAK_API_URL}/drive/v1/privilege/vip"
        me_result = requests.get(url=me_url, headers=login_headers, timeout=5).json()
    except Exception:
        return 3

    if "error" in me_result:
        if me_result['error_code'] == 16:
            logging.info(f"账号{USER[index]}登录过期，正在重新登录")
            login(index)
            login_headers = get_headers(index)
            me_result = requests.get(url=me_url, headers=login_headers, timeout=5).json()
        else:
            logging.error(f"获取vip信息失败{me_result.json()['error_description']}")
            return 3

    if me_result['data']['status'] == 'ok':
        return 0
    elif me_result['data']['status'] == 'invalid':
        return 1
    else:  # 暂未见过
        return 2


# 账号管理功能
def account_manage(update: Update, context: CallbackContext):
    # account l/list --> 账号名称 是否为vip
    # account a/add 账号 密码 --> 添加到USER、PASSWORD开头，pikpak_headers开头加个元素None，保存到config.py
    # account d/delete 账号 --> 删除指定USER\PASSWORD\pikpak_headers
    argv = context.args

    if len(argv) == 0:
        context.bot.send_message(chat_id=update.effective_chat.id, text='【用法】\n罗列账号：/account l/list \[vip]\n添加账号：/account a/add '
                                                                        '账号 密码\n删除账号：/account d/delete 账号1 \[账号2] \[...]\n【示例】\n'
                                                                        '`/account l`\n`/account l vip`\n`/account a 12345678@qq.com '
                                                                        '12345678`\n`/account d 12345678@qq.com`',
                                 parse_mode='Markdown')

    elif argv[0] in ['l', 'list']:
        if len(argv) == 2 and argv[1] == 'vip':
            context.bot.send_message(chat_id=update.effective_chat.id, text=print_user_vip(), parse_mode='Markdown')
        else:
            context.bot.send_message(chat_id=update.effective_chat.id, text=print_user(), parse_mode='Markdown')

    elif argv[0] in ['a', 'add']:
        if not running:
            if len(argv) == 3:  # 三个参数才是正确形式
                USER.insert(0, argv[1])  # 插入账号
                PASSWORD.insert(0, argv[2])  # 插入密码
                pikpak_headers.insert(0, None)  # 设置pikpak_headers
                record_config()  # 记录进入config文件

                print_info = print_user()
                context.bot.send_message(chat_id=update.effective_chat.id, text=print_info, parse_mode='Markdown')
            else:
                context.bot.send_message(chat_id=update.effective_chat.id, text='参数个数错误，请检查！')
        else:
            context.bot.send_message(chat_id=update.effective_chat.id, text='其他命令正在运行，为避免冲突，请稍后再试~')

    elif argv[0] in ['d', 'delete']:
        if not running:
            if len(argv) > 1:
                for each_account in argv[1:]:
                    try:
                        temp_account_index = USER.index(each_account)
                    except ValueError:
                        context.bot.send_message(chat_id=update.effective_chat.id, text=f'账号{each_account}不存在')
                        continue
                    USER.pop(temp_account_index)
                    PASSWORD.pop(temp_account_index)
                    pikpak_headers.pop(temp_account_index)
                    record_config()

                    print_info = print_user()
                    context.bot.send_message(chat_id=update.effective_chat.id, text=print_info, parse_mode='Markdown')
            else:
                context.bot.send_message(chat_id=update.effective_chat.id, text='参数个数错误，请检查！')
        else:
            context.bot.send_message(chat_id=update.effective_chat.id, text='其他命令正在运行，为避免冲突，请稍后再试~')
    else:
        context.bot.send_message(chat_id=update.effective_chat.id, text='未识别的命令，请检查！')


'''
# 想弃用/download命令
# 下载账号下所有文件
def download_main(update: Update, context: CallbackContext, argv: list):
    global running, account_index

    if not len(argv):
        context.bot.send_message(chat_id=update.effective_chat.id, text='【用法】\n/download 账号1 [账号2] [...]')
    else:
        for each_account in argv:
            # 检查输入账号是否存在
            try:
                account_index = the_config['user'].index(each_account)
            except ValueError:
                context.bot.send_message(chat_id=update.effective_chat.id, text=f'账号{each_account}不存在！')
                continue

            # 捕捉请求超时异常
            try:
                login(account_index)  # 先登录，更新headers等信息
                gid = {}  # 记录每个下载任务的gid
                download_headers = {'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.9; rv:50.0) Gecko/20100101 Firefox/50.0'}  # 偶尔会出现aria2下载失败，报ssl错误，试试加上header。没用，好像是我自己aria2版本问题

                # 获取所有文件的相关信息，并推送aria2开始下载
                for name, url, down_file_id, path in get_folder_all_file(folder_id='', path=''):
                    jsonreq = json.dumps({'jsonrpc': '2.0', 'id': 'qwer', 'method': 'aria2.addUri',
                                          'params': [f"token:{the_config['Aria2_secret']}", [url],
                                                     {"dir": the_config['Aria2_download_path'] + '/' + path, "out": f"{name}",
                                                      "header": download_headers, "check-certificate": False}]})

                    push_flag = False
                    # 文件夹的推送下载是网络请求密集地之一，每个链接将尝试？次
                    for _ in range(5):
                        try:
                            response = requests.post(f'{SCHEMA}://{the_config["Aria2_host"]}:{the_config["Aria2_port"]}/jsonrpc', data=jsonreq, timeout=5).json()
                            push_flag = True
                            break
                        except requests.exceptions.ReadTimeout:
                            continue
                    if not push_flag:
                        raise requests.exceptions.ReadTimeout

                    gid[response['result']] = [f'{name}', down_file_id]
                    context.bot.send_message(chat_id=update.effective_chat.id, text=f'{name}推送aria2下载')
                    logging.info(f'{name}推送aria2下载')

                # 准备查询下载进度
                sleep(20)
                # 查询每个gid是否完成
                download_done = False
                complete_file_id = []  # 记录aria2下载成功的文件id
                failed_gid = {}  # 记录下载失败的gid
                while not download_done:
                    temp_gid = gid.copy()
                    for each_gid in gid.keys():
                        # 这里是网络请求最密集的地方之一，一次查询失败跳过即可
                        try:
                            jsonreq = json.dumps({'jsonrpc': '2.0', 'id': 'qwer', 'method': 'aria2.tellStatus',
                                                  'params': [f"token:{the_config['Aria2_secret']}", each_gid,
                                                             ["gid", "status", "errorMessage", "dir"]]})
                            response = requests.post(f'{SCHEMA}://{the_config["Aria2_host"]}:{the_config["Aria2_port"]}/jsonrpc', data=jsonreq,
                                                     timeout=5).json()
                        except requests.exceptions.ReadTimeout:
                            continue

                        try:  # 检查任务状态
                            status = response['result']['status']
                            if status == 'complete':  # 完成了删除对应的gid
                                temp_gid.pop(each_gid)
                                complete_file_id.append(gid[each_gid][1])
                            elif status == 'error':
                                error_message = response["result"]["errorMessage"]
                                # 如果是这两种错误信息，可尝试重新推送aria2下载
                                if error_message in ['No URI available.', 'SSL/TLS handshake failure: SSL I/O error']:
                                    # 再次推送aria2下载
                                    retry_down_name, retry_the_url, _ = get_download_url(file_id=gid[each_gid][1])
                                    # 这只可能是文件，不会是文件夹
                                    jsonreq = json.dumps({'jsonrpc': '2.0', 'id': 'qwer', 'method': 'aria2.addUri',
                                                          'params': [f"token:{the_config['Aria2_secret']}", [retry_the_url],
                                                                     {"dir": response["result"]["dir"], "out": retry_down_name,
                                                                      "header": download_headers, "check-certificate": False}]})
                                    response = requests.post(f'{SCHEMA}://{the_config["Aria2_host"]}:{the_config["Aria2_port"]}/jsonrpc',
                                                             data=jsonreq, timeout=5).json()
                                    # 重新记录gid
                                    temp_gid[response['result']] = [retry_down_name, gid[each_gid][1]]
                                    # 删除旧的gid
                                    temp_gid.pop(each_gid)
                                    # 消息提示
                                    # context.bot.send_message(chat_id=update.effective_chat.id,
                                    #                          text=f'aria2下载{gid[each_gid][0]}出错！错误信息：{error_message}\n此文件已重新推送aria2下载！')
                                    logging.warning(f'aria2下载{gid[each_gid][0]}出错！错误信息：{error_message}\t此文件已重新推送aria2下载！')
                                else:
                                    context.bot.send_message(chat_id=update.effective_chat.id,
                                                             text=f'aria2下载{gid[each_gid][0]}出错！错误信息：{error_message}')
                                    logging.warning(f'aria2下载{gid[each_gid][0]}出错！错误信息：{error_message}')
                                    failed_gid[each_gid] = temp_gid.pop(each_gid)
                        except KeyError:  # 此时任务可能已被删除
                            context.bot.send_message(chat_id=update.effective_chat.id, text=f'aria2下载{gid[each_gid][0]}任务被删除！')
                            logging.warning(f'aria2下载{gid[each_gid][0]}任务被删除！')
                            failed_gid[each_gid] = temp_gid.pop(each_gid)
                    gid = temp_gid
                    if len(gid) == 0:
                        download_done = True
                        print_info = f'aria2下载账号{each_account}下所有文件已完成，共{len(complete_file_id) + len(failed_gid)}个文件，其中{len(complete_file_id)}个成功，{len(failed_gid)}个失败'
                        if failed_gid:
                            print_info += '，下载失败文件为：\n'
                            for values in failed_gid.values():
                                print_info += values[0] + '\n'
                        print_info = print_info.rstrip()
                        context.bot.send_message(chat_id=update.effective_chat.id, text=print_info)
                        logging.info(print_info)
                    else:
                        logging.info(f'aria2下载账号{each_account}下所有文件还未完成...')
                        sleep(10)

                # 完成所有的gid则删除下载成功的文件file_id，注意仅删除下载成功的文件
                delete_files(complete_file_id)
                logging.info(f'已删除账号{each_account}下载成功的网盘文件')
                delete_trash(complete_file_id)
                logging.info(f'已删除账号{each_account}下载成功的回收站文件')
                context.bot.send_message(chat_id=update.effective_chat.id, text=f'账号{each_account}中成功下载的文件的网盘空间已释放')

                # 对于失败的文件，给出解决方案
                if failed_gid:
                    print_info = f'对于下载失败的文件可使用命令：\n`/clean {each_account}`清空此账号下所有文件；或者：\n`/download {each_account}`重试下载此账号下所有文件'
                    context.bot.send_message(chat_id=update.effective_chat.id, text=print_info, parse_mode='Markdown')
                    logging.info(print_info)
            except requests.exceptions.ReadTimeout:
                context.bot.send_message(chat_id=update.effective_chat.id, text=f'账号{each_account}下载请求超时，请稍后重试`/download {each_account}`', parse_mode='Markdown')
                logging.error(f'账号{each_account}下载请求超时，请稍后重试/download {each_account}')
                continue

    running = False


def download(update: Update, context: CallbackContext):
    # return context.bot.send_message(chat_id=update.effective_chat.id, text='/pikpak命令已足够鲁棒，因此/download命令将弃用，若/pikpak命令使用中报错，欢迎反馈bug')

    global running
    argv = context.args

    if not running:
        # 下载类操作可以开个线程防止阻塞
        running = True
        t1 = threading.Thread(target=download_main, name='DownloadAccountThread', args=[update, context, argv])  # 开线程跑下载，防止阻塞
        t1.start()
    else:
        context.bot.send_message(chat_id=update.effective_chat.id, text='有命令正在运行，为避免冲突请稍后再试~')
'''

start_handler = CommandHandler(['start', 'help'], start)
pikpak_handler = CommandHandler('pikpak', pikpak)
clean_handler = CommandHandler(['clean', 'clear'], clean)
account_handler = CommandHandler('account', account_manage)
# download_handler = CommandHandler('download', download)  # download命令在pikpak命令健壮后将弃用

# dispatcher.add_handler(download_handler)
dispatcher.add_handler(account_handler)
dispatcher.add_handler(start_handler)
dispatcher.add_handler(pikpak_handler)
dispatcher.add_handler(clean_handler)

updater.start_polling()
updater.idle()
