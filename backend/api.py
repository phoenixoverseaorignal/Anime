from collections import defaultdict
from json import JSONDecodeError
import requests
from library import JsonLibrary
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import PlainTextResponse, JSONResponse, Response
from typing import Tuple, Dict
from urllib.parse import urlparse
from errors import not_found_404, bad_request_400, internal_server_500
from downloader import Download
from scraper import Animepahe, MyAL
from stream import Stream
from selenium.common.exceptions import TimeoutException
import config
from bs4 import BeautifulSoup
import re


async def search(request: Request):
    """searches for anime

    Args:
        request (Request): accessing the app instance

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
            "episode_details": {
                ep_details : [{
                    episode_no (str) : {
                        "ep_session":str, "snapshot":str(url)
                    }, ...
                }]
                "next_page": str(url) or None,
                "previous_page": str(url) or None,
            }
        }
    """
    anime = request.query_params.get("anime", None)

    if not anime:
        return await bad_request_400(request, msg="Pass an anime name")

    with requests.Session() as session:
        try:
            anime_details = Animepahe().search_anime(session, input_anime=anime)
            anime_details = {
                "jp_name": anime_details.get("title"),
                "no_of_episodes": anime_details.get("episodes"),
                "session": anime_details.get("session"),
                "poster": anime_details.get("poster"),
            }
        except KeyError:
            return await not_found_404(request, msg="anime not found")

        try:
            episodes = _episode_details(session, anime_details.get("session"), "1")
            anime_description = Animepahe().get_anime_description(session, anime_session=anime_details["session"])

            anime_details["episode_details"] = await episodes
            anime_details["description"] = await anime_description
        except TypeError:
            return await not_found_404(request, msg="Anime {}, Not Yet Aired...".format(anime))

    # a function to insert the eng_name at position(index)=1
    def insert(_dict, obj, pos):
        return {
            k: v for k, v in (
                    list(_dict.items())[:pos] + list(obj.items()) + list(_dict.items())[pos:]
            )
        }

    if "eng_anime_name" in anime_details["description"]:
        eng_name = anime_details["description"]["eng_anime_name"]
        del anime_details["description"]["eng_anime_name"]

        anime_details = insert(anime_details, {"eng_name": eng_name}, 1)
    else:
        anime_details = insert(anime_details, {"eng_name": anime_details.get("jp_name")}, 1)

    return JSONResponse(anime_details)


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
    anime_session = request.query_params.get("anime_session", None)
    page = request.query_params.get("page", 1)

    if not anime_session:
        return await bad_request_400(request, msg="Pass anime session")

    with requests.Session() as session:
        try:
            return JSONResponse(await _episode_details(session, anime_session=anime_session, page_no=page))
        except TypeError:
            return await not_found_404(request, msg="Anime, Not yet Aired...")


async def _episode_details(session, anime_session: str, page_no: str) -> Dict[str, str] | TypeError:
    episodes = {"ep_details": []}

    try:
        site_scraper = Animepahe()
        episode_data = site_scraper.get_episode_sessions(session, anime_session=anime_session, page_no=page_no)

        episodes["total_page"] = episode_data.get("last_page")
        if episode_data.get("current_page") <= episode_data.get("last_page"):
            next_page_url = episode_data.get("next_page_url", None)
            if next_page_url:
                next_page_url = next_page_url.replace(site_scraper.api_url,
                                                      f"/ep_details?anime_session={anime_session}&")
                episodes["next_page_url"] = next_page_url
            else:
                episodes["next_page_url"] = next_page_url

            prev_page_url = episode_data.get("prev_page_url", None)
            if prev_page_url:
                prev_page_url = prev_page_url.replace(site_scraper.api_url,
                                                      f"/ep_details?anime_session={anime_session}&")
                episodes["prev_page_url"] = prev_page_url
            else:
                episodes["prev_page_url"] = prev_page_url

            episode_session = episode_data.get("data", None)
            for ep in episode_session:
                episodes["ep_details"].append(
                    {ep["episode"]: {"ep_session": ep["session"], "snapshot": ep["snapshot"]}})
            return episodes
        else:
            episodes["next_page"] = episode_data.get("next_page_url")
            episodes["previous_page"] = f"/ep_details?anime_session={anime_session}&page={episode_data['last_page']}"
            return episodes
    except TypeError:
        raise TypeError


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
    anime_session = request.query_params.get("anime_session", None)
    episode_session = request.query_params.get("ep_session", None)

    if anime_session is None or episode_session is None:
        return await bad_request_400(request, msg="Pass Anime and Episode sessions")

    try:
        stream_data = Animepahe().get_episode_stream_data(episode_session=episode_session, anime_session=anime_session)
        resp = defaultdict(list)
        for data in stream_data:
            for key, val in data.items():
                """
                    stream_dt (dict): {'quality': stream url (str)}
                """
                aud, stream_dt = val["audio"], {key: val["kwik"]}
                resp[aud].append(stream_dt)
        return JSONResponse(resp)
    except JSONDecodeError:
        return await not_found_404(request, msg="Pass valid anime and episode sessions")


async def get_video_url(request: Request):
    try:
        if request.headers.get("content-type", None) != "application/json":
            return await bad_request_400(request, msg="Invalid content type")
        jb = await request.json()

        pahewin_url = jb.get("pahewin_url", None)
        if not pahewin_url:
            return await bad_request_400(request, msg="Invalid JSON body: pass valid pahewin url")

        parsed_url = urlparse(pahewin_url)
        # if url is invalid return await bad_request_400(request, msg="Invalid pahewin url")
        if not all([parsed_url.scheme, parsed_url.netloc]) or "https://pahe.win" not in pahewin_url:
            return await bad_request_400(request, msg="Invalid pahewin url")

        try:
            video_url, file_name = get_video_url_and_name(pahewin_url)
            return JSONResponse({"video_url": video_url, "file_name": file_name}, status_code=200)
        except TypeError:
            return await not_found_404(request, msg="Invalid url")
        except TimeoutException:
            return await internal_server_500(request, msg="Try again after sometime")

    except JSONDecodeError:
        return await bad_request_400(request, msg="Malformed JSON body: pass valid pahewin url")


async def stream(request: Request):
    try:
        if request.headers.get("content-type", None) != "application/json":
            return await bad_request_400(request, msg="Invalid content type")

        jb = await request.json()

        player_name = jb.get("player", None)
        if not player_name:
            return await bad_request_400(request, msg="pass video player_name")

        video_url = jb.get("video_url", None)
        if not video_url:
            return await bad_request_400(request, msg="pass valid video url")
        msg, status_code = play(player_name.lower(), video_url)
        return JSONResponse({"error": msg}, status_code=status_code)
    except JSONDecodeError:
        return await bad_request_400(request, msg="Malformed body: Invalid JSON")


async def download(request: Request):
    # pahewin = request.query_params.get("pw")  # get pahewin url from query parameter
    try:
        if request.headers.get("content-type", None) != "application/json":
            return await bad_request_400(request, msg="Invalid content type")

        jb = await request.json()

        video_url = jb.get("video_url", None)
        if not video_url:
            return await bad_request_400(request, msg="Malformed body: pass valid Pahewin url")

        file_name = jb.get("file_name", None)
        if not file_name or file_name[-3:] != Animepahe.video_extension:
            return await bad_request_400(request, msg="Malformed body: pass valid filename")

        await Download().start_download(url=video_url, file_name=file_name)
        return JSONResponse({"status": "started"})

    except JSONDecodeError:
        return await bad_request_400(request, msg="Malformed body: Invalid JSON")


def get_video_url_and_name(pahewin: str) -> Tuple[str, str]:
    animepahe = Animepahe()
    f_link = animepahe.get_kwik_f_link(pahewin_url=pahewin)
    return animepahe.extract_download_details(animepahe.get_kwik_f_page(f_link), f_link)


async def library(request: Request):
    """

    Args:
        request: Request object consist of client request data

    Returns: JSONResponse Consist of all the files in the library

    """
    return JSONResponse(JsonLibrary().get_all())


def play(player_name: str, video_url: str) -> Tuple[str, int]:
    try:
        Stream.play(player_name, video_url)
        return None, 200
    except Exception as error:
        return error.__str__(), 500


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


async def get_manifest(request: Request):
    kwik_url = request.query_params.get("kwik_url", None)
    if not kwik_url:
        return await bad_request_400(request, msg="kwik url not present")

    headers = {
        'accept': '*/*',
        'accept-language': 'en-GB,en;q=0.9,ja-JP;q=0.8,ja;q=0.7,en-US;q=0.6',
        'origin': 'https://kwik.cx',
        'referer': 'https://kwik.cx/',
    }

    stream_headers = headers.copy()
    stream_headers['referer'] = "https://animepahe.com/"

    stream_response = requests.get(kwik_url, headers=stream_headers)
    bs = BeautifulSoup(stream_response.text, 'html.parser')

    all_scripts = bs.find_all('script')
    pattern = r'\|\|\|.*\'\.'
    pattern_list = (re.findall(pattern, str(all_scripts[6]))[0]).split('|')[88:98]

    uwu_url = 'https://{}-{}.files.nextcdn.org/stream/{}/{}/uwu.m3u8'.format(pattern_list[9], pattern_list[8],
                                                                             pattern_list[3], pattern_list[2])

    response = requests.get(
        uwu_url,
        headers=headers)

    content = response.text

    start_idx, end_idx = content.index("URI="), content.index(".key")
    return PlainTextResponse(content.replace(content[start_idx + 5:end_idx + 4], f"{config.API_SERVER_ADDRESS}/get_key?key={content[start_idx + 5:end_idx + 4]}"))


async def get_mon_key(request: Request):
    key_url = request.query_params.get("key", None)
    print(key_url)
    if not key_url:
        await bad_request_400(request, msg="key not present")

    headers = {
        'accept': '*/*',
        'accept-language': 'en-GB,en;q=0.9,ja-JP;q=0.8,ja;q=0.7,en-US;q=0.6',
        'origin': 'https://kwik.cx',
        'referer': 'https://kwik.cx/',
    }

    resp = requests.get(key_url, headers=headers)
    return Response(resp.content, media_type="application/octet-stream")


routes = [
    Route("/search", endpoint=search, methods=["GET"]),
    Route("/top_anime", endpoint=top_anime, methods=["GET"]),
    Route("/ep_details", endpoint=get_ep_details, methods=["GET"]),
    Route("/stream_details", endpoint=get_stream_details, methods=["GET"]),
    Route("/get_video_url", endpoint=get_video_url, methods=["POST"]),
    Route("/stream", endpoint=stream, methods=["POST"]),
    Route("/download", endpoint=download, methods=["POST"]),
    Route("/library", endpoint=library, methods=["GET"]),
    Route("/get_manifest", endpoint=get_manifest, methods=["GET"]),
    Route('/get_key', endpoint=get_mon_key, methods=["GET"])
]

exception_handlers = {
    400: bad_request_400,
    404: not_found_404,
    500: internal_server_500
}

app = Starlette(
    debug=True,
    routes=routes,
    exception_handlers=exception_handlers,
    on_startup=[JsonLibrary().load_data],
)