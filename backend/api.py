import asyncio
from json import JSONDecodeError
import requests
from video.library import DBLibrary
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import PlainTextResponse, JSONResponse, Response, FileResponse
from typing import Tuple, Dict, List
from errors.http_error import not_found_404, bad_request_400, internal_server_500, service_unavailable_503
from video.downloader import DownloadManager
from scraper import Animepahe, MyAL
from video.streamer import Stream
from config import ServerConfig, FileConfig
from bs4 import BeautifulSoup
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from utils.headers import get_headers
from utils.master_m3u8 import build_master_manifest
from middleware import ErrorHandlerMiddleware, requestValidator
import uvicorn
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles
from urllib.parse import parse_qsl
from utils.init_db import DB
from utils import remove_file
from sqlite3 import IntegrityError


async def LiSA(request: Request):

    return Response("All servers started successfully")


async def search(request: Request):
    """searches for anime

    Args:
        request (Request): request object

    Query Params:
        anime (str): name of anime to search

    Returns:
        JSONResponse: anime details {
            "jp_anime_name":str,
            "eng_anime_name":str,
            "no_of_episodes":int,
            "session":str,
            "poster":str(url),
            "total_pages":int,
            "description": {
                "Type": str, "Episodes": str, "Status": str, "Aired":str, "Season":str, "Duration":str,
            },
            "ep_details": str
        }
    """
    anime = request.query_params.get("anime", None)

    if not anime:
        return await bad_request_400(request, msg="Pass an anime name")

    total_res = int(request.query_params.get("total_res", 9))
    if total_res <= 0:
        total_res = 1
    elif total_res > 9:
        total_res = 9

    try:

        scraper = Animepahe()
        anime_details = scraper.search_anime(input_anime=anime)

        return JSONResponse(scraper.build_search_resp(anime_details[:total_res]))

    except KeyError:
        return await not_found_404(request, msg="anime not found")

    except ValueError:
        return await bad_request_400(request, msg="invalid query parameter: total_res should be type int")


async def get_ep_details(request: Request):
    """get episodes details page number wise

    Args:
        request (Request): accessing the app instance

    Query Params:
        anime_session (str): anime session
        page (int): page number

    Returns:
        JSONResponse: episodes {
            "ep_details": [{
                "episode_number": {"ep_session":str, "snapshot":str}, ...,
            }]
            "next_page": str(url) or None,
            "previous_page": str(url) or None,
        }
    """
    anime_id = request.query_params.get("anime_id", None)

    if anime_id:
        resp = requests.get(f"{Animepahe.site_url}/a/{anime_id}", allow_redirects=False)
        if resp.status_code != 302:
            return await service_unavailable_503(request)
        anime_session = resp.headers["location"].strip(f"{Animepahe.site_url}/anime/")
    else:
        anime_session = request.query_params.get("anime_session", None)

    if not anime_session:
        return await bad_request_400(request, msg="Pass anime session")

    page = request.query_params.get("page", "1")

    try:
        return JSONResponse(await Animepahe().get_episode_details(anime_session=anime_session, page_no=page))
    except TypeError:
        return await not_found_404(request, msg="Anime, Not yet Aired...")
    except JSONDecodeError:
        return await not_found_404(request, msg="Anime not found")


async def get_stream_details(request: Request):
    """getting episode details

    Args:
        request (Request): accessing the app instance

    Query Params:
        anime_session (str): anime session
        episode_session (str): episode session

    Returns:
        JSONResponse: episode details {
            "quality_audio":{"kwik_pahewin":str(url)}, ...
        }
    """
    # anime_session = request.query_params.get("anime_session", None)
    episode_session = request.query_params.get("ep_session", None)

    if not episode_session:  # or episode_session is None:
        return await bad_request_400(request, msg="Pass Episode sessions")

    try:
        return JSONResponse(Animepahe().get_stream_data(episode_session=episode_session))
    except JSONDecodeError:
        return await not_found_404(request, msg="Pass valid anime and episode sessions")


async def stream(request: Request):

    jb = request.state.body

    player_name = jb.get("player", None)
    if not player_name:
        return await bad_request_400(request, msg="pass video player_name")

    manifest_url, _id = jb.get("manifest_url", None), jb.get("id", None)
    video_src = manifest_url or _id
    if not video_src:
        return await bad_request_400(request, msg="pass valid manifest url or video id")
    if _id:
        cur = DB.connection.cursor()
        cur.execute("SELECT file_location FROM progress_tracker WHERE id=?", [_id, ])
        res = cur.fetchone()
        if not res:
            return JSONResponse({"error": "Invalid Id"}, status_code=400)
        video_src = res[0]
        cur.close()

    msg, status_code = await play(player_name.lower(), video_src)
    return JSONResponse({"error": msg}, status_code=status_code)


async def download(request: Request):

    jb = request.state.body

    anime_session = jb.get("anime_session", None)  # get anime_session

    try:

        if anime_session:  # if anime session exists start batch download
            await DownloadManager.schedule(anime_session=anime_session, site="animepahe", page=jb.get("page_no", 1))
        else:

            manifest_url = jb.get("manifest_url", None)  # if anime_session doesn't exists get manifest_url

            if not manifest_url:  # if manifest url doesn't exist (i.e. both session and manifest url are absent)
                return await bad_request_400(request, msg="Malformed body: pass manifest url or anime session")

            await DownloadManager.schedule(manifest_url=parse_qsl(manifest_url)[0][1], site="animepahe")
        return JSONResponse({"status": "started"})
    except ValueError as err:
        return await bad_request_400(request, msg=err.__str__())


async def pause_download(request: Request):
    try:
        task_ids = list(request.state.body.get("id", None))
        if not task_ids:
            return await bad_request_400(request, msg="download id not present")
        await DownloadManager.pause(task_ids)
        return JSONResponse({"msg": "all tasks are successfully paused"})
    except KeyError:
        return await bad_request_400(request, msg="One or more ids are invalid")
    except AttributeError as err_msg:
        return await bad_request_400(request, msg=err_msg.__str__())


async def resume_download(request: Request):

    try:
        task_ids = request.state.body.get("id", None)
        if not task_ids:
            return await bad_request_400(request, msg="download id not present")
        await DownloadManager.resume(task_ids)
        return JSONResponse({"msg": "all tasks are successfully resumed"})
    except KeyError:
        return await bad_request_400(request, msg="One or more ids are invalid")
    except AttributeError as err_msg:
        return await bad_request_400(request, msg=err_msg)


async def cancel_download(request: Request):
    try:
        task_ids = request.state.body.get("id", None)
        if not task_ids:
            return await bad_request_400(request, msg="download id not present")

        await DownloadManager.cancel(task_ids)
        return JSONResponse({"msg": "all tasks are successfully cancelled"})
    except KeyError:
        return await bad_request_400(request, msg="One or more ids are invalid")


async def library(request: Request):
    """

    Args:
        request: Request object consist of client request data

    Returns: JSONResponse Consist of all the files in the library for GET request

    """
    if request.method == "DELETE":
        try:
            _id = int(request.query_params["id"])
            if DBLibrary.data[_id]["status"] != "downloaded":  # if file hasn't been downloaded, raise key-error
                raise KeyError

            file_location = DBLibrary.data[_id]["file_location"]
            DBLibrary.delete(_id)
            remove_file(file_location)
            return Response(status_code=204)
        except KeyError or TypeError:
            return await bad_request_400(request, msg="missing or invalid query parameter: 'id'")

    return JSONResponse(DBLibrary().get_all())


async def play(player_name: str, manifest_url: str) -> Tuple[str, int]:
    try:
        await Stream.play(player_name, manifest_url)
        return None, 200
    except Exception as error:
        return str(error), 400


async def top_anime(request: Request):
    """Get top anime

    Args:
        request (Request): accessing the app instance

    Query Params:
        type (str): either of ['airing', 'upcoming', 'tv', 'movie', 'ova', 'ona', 'special', 'by_popularity', 'favorite']
        limit (str):

    Returns:
        JSONResponse: top_response {
            "<rank>" : {
                "img_url" : (str)url,
                "title" : (str),
                "anime_type" : (str),
                "episodes" : (str),
                "score" : (str),
            },
            ...
            "next_top":"api_server_address/top_anime?type=anime_type&limit=limit"
        }
    """
    anime_type = request.query_params.get("type", None)
    limit = request.query_params.get("limit", "0")

    if not anime_type or anime_type.lower() not in MyAL.anime_types_dict:
        return await bad_request_400(request, msg="Pass valid anime type")

    top_anime_response = MyAL().get_top_anime(anime_type=anime_type, limit=limit)

    if not top_anime_response["next_top"] and not top_anime_response["prev_top"]:
        return await not_found_404(request, msg="limit out of range")

    return JSONResponse(top_anime_response)


async def get_master_manifest(request: Request):
    kwik_urls = request.query_params.get("kwik_url", None)
    if not kwik_urls:
        return await bad_request_400(request, msg="kwik url not present")

    kwik_urls = kwik_urls.split(",")
    if kwik_urls[-1] == "":
        kwik_urls.pop()

    with open(Animepahe.master_manifest_location, "w+") as f:
        try:
            f.write(build_master_manifest(kwik_urls))
        except ValueError as err_msg:
            return await bad_request_400(request, msg=str(err_msg))

    return FileResponse(Animepahe.master_manifest_location, media_type="application/vnd.apple.mpegurl", filename=Animepahe.master_manifest_filename)


async def get_manifest(request: Request):
    kwik_url = request.query_params.get("kwik_url", None)
    if not kwik_url:
        return await bad_request_400(request, msg="kwik url not present")

    try:

        response, uwu_root_domain, _ = await Animepahe().get_manifest_file(kwik_url)

        with open(Animepahe.manifest_location, "w+") as f:
            f.write(response.replace(uwu_root_domain, f"{ServerConfig.API_SERVER_ADDRESS}/proxy?url={uwu_root_domain}"))

        return FileResponse(Animepahe.manifest_location, media_type="application/vnd.apple.mpegurl", filename=Animepahe.manifest_filename)
    except ValueError as err_msg:
        return await bad_request_400(request, msg=str(err_msg))


async def proxy(request: Request):
    """
    This function will proxy request for manifest files, encryption key and video(ts) frames

    """
    actual_url = request.query_params.get("url", None)
    if not actual_url:
        await bad_request_400(request, msg="url not present")

    resp = requests.get(actual_url, headers=get_headers(extra={"origin": "https://kwik.cx", "referer": "https://kwik.cx/", "accept": "*/*"}))
    return Response(resp.content, headers=resp.headers)


async def get_recommendation(request: Request):
    anime_session = request.query_params.get("anime_session", None)
    if not anime_session:
        return await bad_request_400(request, msg="Pass Anime session")

    try:
        return JSONResponse(await Animepahe().get_recommendation(anime_session))

    except ValueError as err_msg:
        return bad_request_400(request, msg=err_msg)

    except AttributeError or IndexError:
        return service_unavailable_503(request, msg="Try Again After Sometime")


async def watchlist(request: Request):
    try:
        if request.method == "GET":
            cur = DB.connection.cursor()
            cur.execute("SELECT * FROM watchlist ORDER BY created_on DESC")
            return JSONResponse({"data": [dict(row) for row in cur.fetchall()]})

        elif request.method == "POST":
            jb = request.state.body

            anime_id = jb["anime_id"]
            ep_details = f"{ServerConfig.API_SERVER_ADDRESS}/ep_details?anime_id={anime_id}"

            cur = DB.connection.cursor()

            cur.execute("INSERT INTO watchlist (anime_id, jp_name, no_of_episodes, type, status, season, year, score, poster, ep_details)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (anime_id, jb["jp_name"], jb["no_of_episodes"], jb["type"], jb["status"], jb["season"],
                         jb["year"], jb["score"], jb["poster"], ep_details))

            DB.connection.commit()
            return JSONResponse(content="Anime successfully added in watch later", status_code=201)

        cur = DB.connection.cursor()
        # id validation is bypassed by choice
        cur.execute("DELETE FROM watchlist where anime_id=?", (request.query_params["anime_id"], ))
        DB.connection.commit()
        return Response(status_code=204)
    except KeyError as _msg:
        return await bad_request_400(request, msg=f"Invalid request: {_msg} not present")
    except IntegrityError:
        return await bad_request_400(request, msg=f"Record already exists")


routes = [
    Route("/", endpoint=LiSA, methods=["GET"]),
    Route("/search", endpoint=search, methods=["GET"]),
    Route("/top_anime", endpoint=top_anime, methods=["GET"]),
    Route("/ep_details", endpoint=get_ep_details, methods=["GET"]),
    Route("/recommendation", endpoint=get_recommendation, methods=["GET"]),
    Route("/stream_detail", endpoint=get_stream_details, methods=["GET"]),
    Route("/stream", endpoint=stream, methods=["POST"]),
    Route("/download", endpoint=download, methods=["POST"]),
    Route("/download/pause", endpoint=pause_download, methods=["POST"]),
    Route("/download/resume", endpoint=resume_download, methods=["POST"]),
    Route("/download/cancel", endpoint=cancel_download, methods=["POST"]),
    Route("/library", endpoint=library, methods=["GET", "DELETE"]),
    Route("/master_manifest", endpoint=get_master_manifest, methods=["GET"]),
    Route("/manifest", endpoint=get_manifest, methods=["GET"]),
    Route("/proxy", endpoint=proxy, methods=["GET"]),
    Route("/watchlist", endpoint=watchlist, methods=["GET", "POST", "DELETE"]),
    Mount('/default', app=StaticFiles(directory=FileConfig.DEFAULT_DIR, check_dir=True), name="static"),
]

exception_handlers = {
    400: bad_request_400,
    404: not_found_404,
    500: internal_server_500
}

middleware = [
    Middleware(CORSMiddleware, allow_methods=["*"], allow_headers=["*"], allow_origins=["*"], allow_credentials=True),
    Middleware(requestValidator),
    Middleware(ErrorHandlerMiddleware)
]

app = Starlette(
    debug=True,
    routes=routes,
    exception_handlers=exception_handlers,
    middleware=middleware,
    on_startup=[DBLibrary().load_data],
)


def start_api_server(port: int):
    uvicorn.run(app, port=port)
