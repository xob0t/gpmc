import time
from collections.abc import Generator, Sequence
from pathlib import Path
from typing import IO, Any, Literal
from urllib.parse import parse_qsl

import requests
from blackboxprotobuf import decode_message, encode_message
from requests.adapters import HTTPAdapter, Retry

from . import message_types
from .exceptions import UploadRejectedError

DEFAULT_TIMEOUT = 60
RETRIES = 10


class Api:
    def __init__(
        self,
        auth_data: str,
        proxy: str = "",
        language: str = "en_US",
        timeout: int = DEFAULT_TIMEOUT,
    ) -> None:
        """
        Initialize the Google Photos mobile api.
        """
        self.proxy = proxy
        self.timeout = timeout
        self.android_api_version = 28
        self.model = "Pixel XL"
        self.make = "Google"
        self.client_version_code = 49029607
        self.user_agent = f"com.google.android.apps.photos/{self.client_version_code} (Linux; U; Android 9; en_US; Pixel XL; Build/PQ2A.190205.001; Cronet/127.0.6510.5) (gzip)"
        self.language = language
        self.auth_data = auth_data.strip()
        self.auth_response_cache: dict[str, str] = {"Expiry": "0", "Auth": ""}

    @property
    def bearer_token(self) -> str:
        """Property that automatically checks and renews the auth token if expired."""
        if int(self.auth_response_cache.get("Expiry", "0")) <= int(time.time()):
            self.auth_response_cache = self._get_auth_token()
        if token := self.auth_response_cache.get("Auth", ""):
            return token
        raise RuntimeError("Auth response does not contain bearer token")

    def _new_session(self) -> requests.Session:
        """Create a new request session with retry mechanism"""
        # https://stackoverflow.com/questions/23267409/how-to-implement-retry-mechanism-into-python-requests-library
        s = requests.Session()
        retries = Retry(total=RETRIES, backoff_factor=1, status_forcelist=[502, 503, 504])
        adapter = HTTPAdapter(max_retries=retries)
        s.mount("http://", adapter)
        s.mount("https://", adapter)
        s.proxies = {
            "http": self.proxy,
            "https": self.proxy,
        }
        if self.proxy:
            s.verify = False
        return s

    def _get_auth_token(self) -> dict[str, str]:
        """
        Send auth request to get bearer token.

        Returns:
            Dict[str, str]: Parsed authentication response with token and other details.

        Raises:
            requests.HTTPError: If the api request fails.
        """
        auth_data_dict = dict(parse_qsl(self.auth_data))

        # this dict has a purpose, just sending `auth_data_dict` can result in auth request that returns encrypted token
        # building it manually should prevent this
        auth_request_data = {
            "androidId": auth_data_dict["androidId"],
            "app": "com.google.android.apps.photos",
            "client_sig": auth_data_dict["client_sig"],
            "callerPkg": "com.google.android.apps.photos",
            "callerSig": auth_data_dict["callerSig"],
            "device_country": auth_data_dict["device_country"],
            "Email": auth_data_dict["Email"],
            "google_play_services_version": auth_data_dict["google_play_services_version"],
            "lang": auth_data_dict["lang"],
            "oauth2_foreground": auth_data_dict["oauth2_foreground"],
            "sdk_version": auth_data_dict["sdk_version"],
            "service": auth_data_dict["service"],
            "Token": auth_data_dict["Token"],
        }

        headers = {
            "Accept-Encoding": "gzip",
            "app": "com.google.android.apps.photos",
            "Connection": "Keep-Alive",
            "Content-Type": "application/x-www-form-urlencoded",
            "device": auth_request_data["androidId"],
            "User-Agent": "GoogleAuth/1.4 (Pixel XL PQ2A.190205.001); gzip",
        }

        with self._new_session() as session:
            response = session.post(
                "https://android.googleapis.com/auth",
                headers=headers,
                data=auth_request_data,
                timeout=self.timeout,
            )

        response.raise_for_status()

        parsed_auth_response = {}
        for line in response.text.splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                parsed_auth_response[key] = value

        return parsed_auth_response

    def get_upload_token(self, sha_hash_b64: str, file_size: int) -> str:
        """
        Obtain an upload token from the Google Photos API.

        Args:
            sha_hash_b64: Base64-encoded SHA-1 hash of the file.
            file_size: Size of the file in bytes.

        Returns:
            str: Upload token for the file.

        Raises:
            requests.HTTPError: If the api request fails.
        """

        proto_body = {"1": 2, "2": 2, "3": 1, "4": 3, "7": file_size}

        serialized_data = encode_message(proto_body, message_types.GET_UPLOAD_TOKEN)  # type: ignore

        headers = {
            "Accept-Encoding": "gzip",
            "Accept-Language": self.language,
            "Content-Type": "application/x-protobuf",
            "User-Agent": self.user_agent,
            "Authorization": f"Bearer {self.bearer_token}",
            "X-Goog-Hash": f"sha1={sha_hash_b64}",
            "X-Upload-Content-Length": str(file_size),
        }
        with self._new_session() as session:
            response = session.post(
                "https://photos.googleapis.com/data/upload/uploadmedia/interactive",
                headers=headers,
                data=serialized_data,
                timeout=self.timeout,
            )
        response.raise_for_status()
        return response.headers["X-GUploader-UploadID"]

    def find_remote_media_by_hash(self, sha1_hash: bytes) -> str | None:
        """
        Check library for existing files with the hash.

        Args:
            sha1_hash: SHA-1 hash of the file.

        Returns:
            str: Media key of the existing file, or None if not found.

        Raises:
            requests.HTTPError: If the api request fails.
        """

        proto_body = {"1": {"1": {"1": sha1_hash}, "2": {}}}
        serialized_data = encode_message(proto_body, message_types.FIND_REMOTE_MEDIA_BY_HASH)  # type: ignore
        headers = {
            "Accept-Encoding": "gzip",
            "Accept-Language": self.language,
            "Content-Type": "application/x-protobuf",
            "User-Agent": self.user_agent,
            "Authorization": f"Bearer {self.bearer_token}",
        }
        with self._new_session() as session:
            response = session.post(
                "https://photosdata-pa.googleapis.com/6439526531001121323/5084965799730810217",
                headers=headers,
                data=serialized_data,
                timeout=self.timeout,
            )
        response.raise_for_status()

        decoded_message, _ = decode_message(response.content)
        media_key = decoded_message["1"].get("2", {}).get("2", {}).get("1", None)
        return media_key

    def upload_file(self, file: str | Path | bytes | IO[bytes] | Generator[bytes, None, None], upload_token: str) -> dict:
        """
        Upload a file to Google Photos.

        Args:
            file: The file to upload. Can be a path (str or Path), bytes, BufferedReader, or a generator yielding bytes.
            upload_token Upload token from `get_upload_token()`.

        Returns:
            dict: Decoded api response.

        Raises:
            requests.HTTPError: If the api request fails.
        """

        headers = {
            "Accept-Encoding": "gzip",
            "Accept-Language": self.language,
            "User-Agent": self.user_agent,
            "Authorization": f"Bearer {self.bearer_token}",
        }

        with self._new_session() as session:
            if isinstance(file, (str, Path)):
                with Path(file).open("rb") as f:
                    response = session.put(
                        f"https://photos.googleapis.com/data/upload/uploadmedia/interactive?upload_id={upload_token}",
                        headers=headers,
                        timeout=self.timeout,
                        data=f,
                    )
            else:
                response = session.put(
                    f"https://photos.googleapis.com/data/upload/uploadmedia/interactive?upload_id={upload_token}",
                    headers=headers,
                    timeout=self.timeout,
                    data=file,
                )

        response.raise_for_status()

        upload_response_decoded, _ = decode_message(response.content)
        return upload_response_decoded

    def commit_upload(
        self,
        upload_response_decoded: dict[str, Any],
        file_name: str,
        sha1_hash: bytes,
        quality: Literal["original", "saver"] = "original",
        make: str | None = None,
        model: str | None = None,
        upload_timestamp: int | None = None,
    ) -> str:
        """
        Commit the upload.

        Args:
            upload_response_decoded: Decoded upload response.
            file_name: Name of the uploaded file.
            sha1_hash: SHA-1 hash of the file.
            quality: Quality setting for the upload. Defaults to "original".
            make: Device manufacturer name. Overrides client's make.
            model: Device model name. Overrides client's model.

        Returns:
            str: Media key of the uploaded file.

        Raises:
            requests.HTTPError: If the api request fails.
        """

        if make is None:
            make = self.make
        if model is None:
            model = self.model

        quality_map = {"saver": 1, "original": 3}
        upload_timestamp = upload_timestamp or int(time.time())
        unknown_int = 46000000

        proto_body = {
            "1": {
                "1": upload_response_decoded,
                "2": file_name,
                "3": sha1_hash,
                "4": {"1": upload_timestamp, "2": unknown_int},
                "7": quality_map[quality],
                "10": 1,
            },
            "2": {"3": model, "4": make, "5": self.android_api_version},
            "3": bytes([1, 3]),
        }

        serialized_data = encode_message(proto_body, message_types.COMMIT_UPLOAD)  # type: ignore

        headers = {
            "Accept-Encoding": "gzip",
            "Accept-Language": self.language,
            "Content-Type": "application/x-protobuf",
            "User-Agent": self.user_agent,
            "Authorization": f"Bearer {self.bearer_token}",
            "x-goog-ext-173412678-bin": "CgcIAhClARgC",
            "x-goog-ext-174067345-bin": "CgIIAg==",
        }
        with self._new_session() as session:
            response = session.post(
                "https://photosdata-pa.googleapis.com/6439526531001121323/16538846908252377752",
                headers=headers,
                data=serialized_data,
                timeout=self.timeout,
            )
        response.raise_for_status()
        decoded_message, _ = decode_message(response.content)
        try:
            media_key = decoded_message["1"]["3"]["1"]
        except KeyError as e:
            raise UploadRejectedError("File upload rejected by api") from e
        return media_key

    def move_remote_media_to_trash(self, dedup_keys: Sequence[str]) -> dict:
        """
        Move remote media items to the trash using deduplication keys.

        Args:
            dedup_keys: Deduplication keys for the media items to be trashed.

        Returns:
            dict: Api response message.

        Raises:
            requests.HTTPError: If the api request fails.
        """

        proto_body = {
            "2": 1,
            "3": dedup_keys,
            "4": 1,
            "8": {"4": {"2": {}, "3": {"1": {}}, "4": {}, "5": {"1": {}}}},
            "9": {"1": 5, "2": {"1": self.client_version_code, "2": str(self.android_api_version)}},
        }
        serialized_data = encode_message(proto_body, message_types.MOVE_TO_TRASH)  # type: ignore
        headers = {
            "Accept-Encoding": "gzip",
            "Accept-Language": self.language,
            "Content-Type": "application/x-protobuf",
            "User-Agent": self.user_agent,
            "Authorization": f"Bearer {self.bearer_token}",
        }
        with self._new_session() as session:
            response = session.post(
                "https://photosdata-pa.googleapis.com/6439526531001121323/17490284929287180316",
                headers=headers,
                data=serialized_data,
                timeout=self.timeout,
            )
        response.raise_for_status()

        decoded_message, _ = decode_message(response.content)
        return decoded_message

    def delete_remote_media_permanently(self, dedup_keys: Sequence[str]) -> dict:
        """
        Permanently delete remote media items using deduplication keys.

        Args:
            dedup_keys: Deduplication keys for the media items to be permanently deleted.

        Returns:
            dict: Api response message.

        Raises:
            requests.HTTPError: If the api request fails.
        """

        proto_body = {
            "2": 2,
            "3": dedup_keys,
            "4": 2,
            "8": {"4": {"2": {}, "3": {"1": {}}, "4": {}, "5": {"1": {}}}},
            "9": ""
        }
        serialized_data = encode_message(proto_body, message_types.DELETE_PERMANENTLY)
        headers = {
            "Accept-Encoding": "gzip",
            "Accept-Language": self.language,
            "Content-Type": "application/x-protobuf",
            "User-Agent": self.user_agent,
            "Authorization": f"Bearer {self.bearer_token}",
        }

        with self._new_session() as session:
            response = session.post(
                "https://photosdata-pa.googleapis.com/6439526531001121323/17490284929287180316",
                headers=headers,
                data=serialized_data,
                timeout=self.timeout,
            )
        response.raise_for_status()

        decoded_message, _ = decode_message(response.content)
        return decoded_message

    def create_album(self, album_name: str, media_keys: Sequence[str]) -> str:
        """Create new album with media.

        Args:
            album_name: Album name.
            media_keys: Media keys of the media items to be added to album.

        Returns:
            str: Album media key.

        Raises:
            requests.HTTPError: If the api request fails.
        """

        proto_body = {
            "1": album_name,
            "2": int(time.time()),
            "3": 1,
            "4": [{"1": {"1": key}} for key in media_keys],
            "6": {},
            "7": {"1": 3},
            "8": {"3": self.model, "4": self.make, "5": self.android_api_version},
        }

        serialized_data = encode_message(proto_body, message_types.CREATE_ALBUM)  # type: ignore

        headers = {
            "Accept-Encoding": "gzip",
            "Accept-Language": self.language,
            "Content-Type": "application/x-protobuf",
            "User-Agent": self.user_agent,
            "Authorization": f"Bearer {self.bearer_token}",
            "x-goog-ext-173412678-bin": "CgcIAhClARgC",
            "x-goog-ext-174067345-bin": "CgIIAg==",
        }
        with self._new_session() as session:
            response = session.post(
                "https://photosdata-pa.googleapis.com/6439526531001121323/8386163679468898444",
                headers=headers,
                data=serialized_data,
                timeout=self.timeout,
            )
        response.raise_for_status()

        decoded_message, _ = decode_message(response.content)
        return decoded_message["1"]["1"]

    def add_media_to_album(self, album_media_key: str, media_keys: Sequence[str]) -> dict:
        """Add media to an album.

        Args:
            album_media_key: Target album media key.
            media_keys: Media keys of the media items to be added to album.

        Returns:
            dict: Decoded api response.

        Raises:
            requests.HTTPError: If the api request fails.
        """

        proto_body = {
            "1": list(media_keys),
            "2": album_media_key,
            "5": {"1": 2},
            "6": {"3": self.model, "4": self.make, "5": self.android_api_version},
            "7": int(time.time()),
        }
        serialized_data = encode_message(proto_body, message_types.ADD_MEDIA_TO_ALBUM)  # type: ignore

        headers = {
            "Accept-Encoding": "gzip",
            "Accept-Language": self.language,
            "Content-Type": "application/x-protobuf",
            "User-Agent": self.user_agent,
            "Authorization": f"Bearer {self.bearer_token}",
            "x-goog-ext-173412678-bin": "CgcIAhClARgC",
            "x-goog-ext-174067345-bin": "CgIIAg==",
        }
        with self._new_session() as session:
            response = session.post(
                "https://photosdata-pa.googleapis.com/6439526531001121323/484917746253879292",
                headers=headers,
                data=serialized_data,
                timeout=self.timeout,
            )
        response.raise_for_status()

        decoded_message, _ = decode_message(response.content)
        return decoded_message

    def get_library_state(self, sync_token: str = "") -> dict:
        """Get library state

        Args:
            sync_token: Previously received sync_token (CURRENT_SYNC/NEXT_SYNC in Google Photos app).

        Returns:
            dict: Decoded api response.
        """
        headers = {
            "accept-encoding": "gzip",
            "Accept-Language": self.language,
            "content-type": "application/x-protobuf",
            "User-Agent": self.user_agent,
            "Authorization": f"Bearer {self.bearer_token}",
            "x-goog-ext-173412678-bin": "CgcIAhClARgC",
            "x-goog-ext-174067345-bin": "CgIIAg==",
        }

        proto_body = {
            "1": {
                "1": {
                    "1": {
                        "1": {},
                        "3": {},
                        "4": {},
                        "5": {"1": {}, "2": {}, "3": {}, "4": {}, "5": {}, "7": {}},
                        "6": {},
                        "7": {"2": {}},
                        "15": {},
                        "16": {},
                        "17": {},
                        "19": {},
                        "20": {},
                        "21": {"5": {"3": {}}, "6": {}},
                        "25": {},
                        "30": {"2": {}},
                        "31": {},
                        "32": {},
                        "33": {"1": {}},
                        "34": {},
                        "36": {},
                        "37": {},
                        "38": {},
                        "39": {},
                        "40": {},
                        "41": {},
                    },
                    "5": {
                        "2": {"2": {"3": {"2": {}}, "4": {"2": {}, "4": {}}}, "4": {"2": {"2": 1}}, "5": {"2": {}}, "6": 1},
                        "3": {"2": {"3": {}, "4": {}}, "3": {"2": {}, "3": {"2": 1, "3": {}}}, "4": {}, "5": {"2": {"2": 1}}, "7": {}},
                        "4": {"2": {"2": {}}},
                        "5": {"1": {"2": {"3": {}, "4": {}}, "3": {"2": {}, "3": {"2": 1, "3": {}}}}, "3": 1},
                    },
                    "8": {},
                    "9": {
                        "2": {},
                        "3": {"1": {}, "2": {}},
                        "4": {"1": {"3": {"1": {"1": {"5": {"1": {}}, "6": {}, "7": {}}, "2": {}, "3": {"1": {"5": {"1": {}}, "6": {}, "7": {}}, "2": {}}}}, "4": {"1": {"2": {}}}}},
                    },
                    "11": {"2": {}, "3": {}, "4": {"2": {"1": 1, "2": 2}}},
                    "12": {},
                    "14": {"2": {}, "3": {}, "4": {"2": {"1": 1, "2": 2}}},
                    "15": {"1": {}, "4": {}},
                    "17": {"1": {}, "4": {}},
                    "19": {"2": {}, "3": {}, "4": {"2": {"1": 1, "2": 2}}},
                    "21": {"1": {}},
                    "22": {},
                    "23": {},
                    "24": {},
                },
                "2": {
                    "1": {
                        "2": {},
                        "3": {},
                        "4": {},
                        "5": {},
                        "6": {"1": {}, "2": {}, "3": {}, "4": {}, "5": {}, "7": {}},
                        "7": {},
                        "8": {},
                        "10": {},
                        "12": {},
                        "13": {"2": {}, "3": {}},
                        "15": {"1": {}},
                        "18": {},
                    },
                    "4": {"1": {}},
                    "9": {},
                    "11": {"1": {"1": {}, "4": {}, "5": {}, "6": {}, "9": {}}},
                    "14": {"1": {"1": {"1": {}, "2": {"2": {"1": {"1": {}}, "3": {}}}, "3": {"4": {"1": {"1": {}}, "3": {}}, "5": {"1": {"1": {}}, "3": {}}}}, "2": {}}},
                    "17": {},
                    "18": {"1": {}, "2": {"1": {}}},
                    "20": {"2": {"1": {}, "2": {}}},
                    "22": {},
                    "23": {},
                    "24": {},
                },
                "3": {
                    "2": {},
                    "3": {
                        "2": {},
                        "3": {},
                        "7": {},
                        "8": {},
                        "14": {"1": {}},
                        "16": {},
                        "17": {"2": {}},
                        "18": {},
                        "19": {},
                        "20": {},
                        "21": {},
                        "22": {},
                        "23": {},
                        "27": {"1": {}, "2": {"1": {}}},
                        "29": {},
                        "30": {},
                        "31": {},
                        "32": {},
                        "34": {},
                        "37": {},
                        "38": {},
                        "39": {},
                        "41": {},
                        "43": {"1": {}},
                        "45": {"1": {"1": {}}},
                        "46": {"1": {}, "2": {}, "3": {}},
                        "47": {},
                    },
                    "4": {"2": {}, "3": {"1": {}}, "4": {}, "5": {"1": {}}},
                    "7": {},
                    "12": {},
                    "13": {},
                    "14": {"1": {}, "2": {"1": {}, "2": {"1": {}}, "3": {}, "4": {"1": {}}}, "3": {"1": {}, "2": {"1": {}}, "3": {}, "4": {}}},
                    "15": {},
                    "16": {"1": {}},
                    "18": {},
                    "19": {"4": {"2": {}}, "6": {"2": {}, "3": {}}, "7": {"2": {}, "3": {}}, "8": {}, "9": {}},
                    "20": {},
                    "22": {},
                    "24": {},
                    "25": {},
                    "26": {},
                },
                "6": sync_token,
                "7": 2,
                "9": {"1": {"2": {"1": {}, "2": {}}}, "2": {"3": {"2": 1}}, "3": {"2": {}}, "4": {}, "7": {"1": {}}, "8": {"1": 2, "2": "\x01\x02\x03\x05\x06\x07"}, "9": {}, "11": {"1": {}}},
                "11": [1, 2, 6],
                "12": {"2": {"1": {}, "2": {}}, "3": {"1": {}}, "4": {}},
                "13": {},
                "15": {"3": {"1": 1}},
                "18": {"169945741": {"1": {"1": {"4": [2, 1, 6, 8, 10, 15, 18, 13, 17, 19, 14, 20], "5": 6, "6": 2, "7": 1, "8": 2, "11": 3, "12": 1, "13": 3, "15": 1, "16": 1, "17": 1, "18": 2}}}},
                "19": {"1": {"1": {}, "2": {}}, "2": {"1": [1, 2, 4, 6, 5, 7]}, "3": {"1": {}, "2": {}}, "5": {"1": {}, "2": {}}, "6": {"1": {}}, "7": {"1": {}, "2": {}}, "8": {"1": {}}},
                "20": {
                    "1": 1,
                    "2": "",
                    "3": {
                        "1": "type.googleapis.com/photos.printing.client.PrintingPromotionSyncOptions",
                        "2": {"1": {"4": [2, 1, 6, 8, 10, 15, 18, 13, 17, 19, 14, 20], "5": 6, "6": 2, "7": 1, "8": 2, "11": 3, "12": 1, "13": 3, "15": 1, "16": 1, "17": 1, "18": 2}},
                    },
                },
                "21": {
                    "2": {"2": {"4": {}}, "4": {}, "5": {}},
                    "3": {"2": {"1": 1}, "4": {"2": {}}},
                    "5": {"1": {}},
                    "6": {"1": {}, "2": {"1": {}}},
                    "7": {"1": 2, "2": "\x01\x07\x08\t\n\r\x0e\x0f\x11\x13\x14\x16\x17-./01:\x06\x18267;>?@A89<GBED", "3": "\x01"},
                    "8": {"3": {"1": {"1": {"2": {"1": 1}, "4": {"2": {}}}}, "3": {}}, "4": {"1": {}}, "5": {"1": {"2": {"1": 1}, "4": {"2": {}}}}},
                    "9": {"1": {}},
                    "10": {"1": {"1": {}}, "3": {}, "5": {}, "6": {"1": {}}, "7": {}, "9": {}, "10": {}},
                    "11": {},
                    "12": {},
                    "13": {},
                    "14": {},
                    "16": {"1": {}},
                },
                "22": {"1": 1, "2": "107818234414673686888"},
                "25": {"1": {"1": {"1": {"1": {}}}}, "2": {}},
                "26": {},
            },
            "2": {"1": {"1": {"1": {"1": {}}, "2": {}}}, "2": {}},
        }
        serialized_data = encode_message(proto_body, message_types.GET_LIB_STATE)  # type: ignore

        with self._new_session() as session:
            response = session.post(
                "https://photosdata-pa.googleapis.com/6439526531001121323/18047484249733410717",
                headers=headers,
                data=serialized_data,
                timeout=self.timeout,
            )

        response.raise_for_status()

        decoded_message, _ = decode_message(response.content, message_type=message_types.LIB_STATE_RESPONSE_FIX)  # type: ignore
        return decoded_message

    def get_library_page_init(self, resume_token: str = "") -> dict:
        """Get library state page during init process

        Args:
            resume_token: Resume token for pagination (INITIAL_RESUME in Google Photos app).

        Returns:
            dict: Decoded api response.
        """
        headers = {
            "accept-encoding": "gzip",
            "Accept-Language": self.language,
            "content-type": "application/x-protobuf",
            "User-Agent": self.user_agent,
            "Authorization": f"Bearer {self.bearer_token}",
            "x-goog-ext-173412678-bin": "CgcIAhClARgC",
            "x-goog-ext-174067345-bin": "CgIIAg==",
        }

        proto_body = {
            "1": {
                "1": {
                    "1": {
                        "1": {},
                        "3": {},
                        "4": {},
                        "5": {"1": {}, "2": {}, "3": {}, "4": {}, "5": {}, "7": {}},
                        "6": {},
                        "7": {"2": {}},
                        "15": {},
                        "16": {},
                        "17": {},
                        "19": {},
                        "20": {},
                        "21": {"5": {"3": {}}, "6": {}},
                        "25": {},
                        "30": {"2": {}},
                        "31": {},
                        "32": {},
                        "33": {"1": {}},
                        "34": {},
                        "36": {},
                        "37": {},
                        "38": {},
                        "39": {},
                        "40": {},
                        "41": {},
                    },
                    "5": {
                        "2": {"2": {"3": {"2": {}}, "4": {"2": {}}}, "4": {"2": {"2": 1}}, "5": {"2": {}}, "6": 1},
                        "3": {"2": {"3": {}, "4": {}}, "3": {"2": {}, "3": {"2": 1}}, "4": {}, "5": {"2": {"2": 1}}, "7": {}},
                        "4": {"2": {"2": {}}},
                        "5": {"1": {"2": {"3": {}, "4": {}}, "3": {"2": {}, "3": {"2": 1}}}, "3": 1},
                    },
                    "8": {},
                    "9": {"2": {}, "3": {"1": {}, "2": {}}, "4": {"1": {"3": {"1": {"1": {"5": {"1": {}}, "6": {}}, "2": {}, "3": {"1": {"5": {"1": {}}, "6": {}}, "2": {}}}}, "4": {"1": {"2": {}}}}}},
                    "11": {"2": {}, "3": {}, "4": {"2": {"1": 1, "2": 2}}},
                    "12": {},
                    "14": {"2": {}, "3": {}, "4": {"2": {"1": 1, "2": 2}}},
                    "15": {"1": {}, "4": {}},
                    "17": {"1": {}, "4": {}},
                    "19": {"2": {}, "3": {}, "4": {"2": {"1": 1, "2": 2}}},
                    "22": {},
                    "23": {},
                },
                "2": {
                    "1": {
                        "2": {},
                        "3": {},
                        "4": {},
                        "5": {},
                        "6": {"1": {}, "2": {}, "3": {}, "4": {}, "5": {}, "7": {}},
                        "7": {},
                        "8": {},
                        "10": {},
                        "12": {},
                        "13": {"2": {}, "3": {}},
                        "15": {"1": {}},
                        "18": {},
                    },
                    "4": {"1": {}},
                    "9": {},
                    "11": {"1": {"1": {}, "4": {}, "5": {}, "6": {}, "9": {}}},
                    "14": {"1": {"1": {"1": {}, "2": {"2": {"1": {"1": {}}, "3": {}}}, "3": {"4": {"1": {"1": {}}, "3": {}}, "5": {"1": {"1": {}}, "3": {}}}}, "2": {}}},
                    "17": {},
                    "18": {"1": {}, "2": {"1": {}}},
                    "20": {"2": {"1": {}, "2": {}}},
                    "23": {},
                },
                "3": {
                    "2": {},
                    "3": {
                        "2": {},
                        "3": {},
                        "7": {},
                        "8": {},
                        "14": {"1": {}},
                        "16": {},
                        "17": {"2": {}},
                        "18": {},
                        "19": {},
                        "20": {},
                        "21": {},
                        "22": {},
                        "23": {},
                        "27": {"1": {}, "2": {"1": {}}},
                        "29": {},
                        "30": {},
                        "31": {},
                        "32": {},
                        "34": {},
                        "37": {},
                        "38": {},
                        "39": {},
                        "41": {},
                    },
                    "4": {"2": {}, "3": {}, "4": {}},
                    "7": {},
                    "12": {},
                    "13": {},
                    "14": {"1": {}, "2": {"1": {}, "2": {"1": {}}, "3": {}, "4": {"1": {}}}, "3": {"1": {}, "2": {"1": {}}, "3": {}, "4": {}}},
                    "15": {},
                    "16": {"1": {}},
                    "18": {},
                    "19": {"4": {"2": {}}, "6": {"2": {}, "3": {}}, "7": {"2": {}, "3": {}}, "8": {}},
                    "20": {},
                    "24": {},
                    "25": {},
                },
                "4": resume_token,
                "7": 2,
                "9": {"1": {"2": {"1": {}, "2": {}}}, "2": {"3": {"2": 1}}, "3": {"2": {}}, "4": {}, "7": {"1": {}}, "8": {"1": 2, "2": "\x01\x02\x03\x05\x06"}, "9": {}},
                "11": [1, 2],
                "12": {"2": {"1": {}, "2": {}}, "3": {"1": {}}, "4": {}},
                "13": {},
                "15": {"3": {"1": 1}},
                "18": {"169945741": {"1": {"1": {"4": [2, 1, 6, 8, 10, 15, 18, 13, 17, 19, 14, 20], "5": 6, "6": 2, "7": 1, "8": 2, "11": 3, "12": 1, "13": 3, "15": 1, "16": 1, "17": 1, "18": 2}}}},
                "19": {"1": {"1": {}, "2": {}}, "2": {"1": [1, 2, 4, 6, 5, 7]}, "3": {"1": {}, "2": {}}, "5": {"1": {}, "2": {}}, "6": {"1": {}}, "7": {"1": {}, "2": {}}, "8": {"1": {}}},
                "20": {
                    "1": 1,
                    "3": {
                        "1": "type.googleapis.com/photos.printing.client.PrintingPromotionSyncOptions",
                        "2": {"1": {"4": [2, 1, 6, 8, 10, 15, 18, 13, 17, 19, 14, 20], "5": 6, "6": 2, "7": 1, "8": 2, "11": 3, "12": 1, "13": 3, "15": 1, "16": 1, "17": 1, "18": 2}},
                    },
                },
                "21": {
                    "2": {"2": {}, "4": {}, "5": {}},
                    "3": {"2": {"1": 1}},
                    "5": {"1": {}},
                    "6": {"1": {}, "2": {"1": {}}},
                    "7": {"1": 2, "2": "\x01\x07\x08\t\n\r\x0e\x0f\x11\x13\x14\x16\x17-./01:\x06\x18267;>?@A89<", "3": "\x01"},
                    "8": {"3": {"1": {"1": {"2": {"1": 1}}}}, "4": {"1": {}}},
                    "9": {"1": {}},
                    "10": {"1": {"1": {}}, "3": {}, "5": {}, "6": {"1": {}}, "7": {}, "9": {}, "10": {}},
                    "11": {},
                    "12": {},
                    "13": {},
                },
                "22": {"1": 2},
                "25": {"1": {"1": {"1": {"1": {}}}}, "2": {}},
            },
            "2": {"1": {"1": {"1": {"1": {}}, "2": {}}}, "2": {}},
        }
        serialized_data = encode_message(proto_body, message_types.GET_LIB_PAGE_INIT)  # type: ignore

        with self._new_session() as session:
            response = session.post(
                "https://photosdata-pa.googleapis.com/6439526531001121323/18047484249733410717",
                headers=headers,
                data=serialized_data,
                timeout=self.timeout,
            )

        response.raise_for_status()

        decoded_message, _ = decode_message(response.content, message_type=message_types.LIB_STATE_RESPONSE_FIX)  # type: ignore
        return decoded_message

    def get_library_page(self, resume_token: str = "", sync_token: str = "") -> dict:
        """Get library state page

        Args:
            resume_token: Resume token for pagination (DELTA_RESUME in Google Photos app).
            sync_token: Sync token for the delta sync context.

        Returns:
            dict: Decoded api response.
        """
        headers = {
            "accept-encoding": "gzip",
            "Accept-Language": self.language,
            "content-type": "application/x-protobuf",
            "User-Agent": self.user_agent,
            "Authorization": f"Bearer {self.bearer_token}",
            "x-goog-ext-173412678-bin": "CgcIAhClARgC",
            "x-goog-ext-174067345-bin": "CgIIAg==",
        }

        proto_body = {
            "1": {
                "1": {
                    "1": {
                        "1": {},
                        "3": {},
                        "4": {},
                        "5": {"1": {}, "2": {}, "3": {}, "4": {}, "5": {}, "7": {}},
                        "6": {},
                        "7": {"2": {}},
                        "15": {},
                        "16": {},
                        "17": {},
                        "19": {},
                        "20": {},
                        "21": {"5": {"3": {}}, "6": {}},
                        "25": {},
                        "30": {"2": {}},
                        "31": {},
                        "32": {},
                        "33": {"1": {}},
                        "34": {},
                        "36": {},
                        "37": {},
                        "38": {},
                        "39": {},
                        "40": {},
                        "41": {},
                    },
                    "5": {
                        "2": {"2": {"3": {"2": {}}, "4": {"2": {}}}, "4": {"2": {"2": 1}}, "5": {"2": {}}, "6": 1},
                        "3": {"2": {"3": {}, "4": {}}, "3": {"2": {}, "3": {"2": 1}}, "4": {}, "5": {"2": {"2": 1}}, "7": {}},
                        "4": {"2": {"2": {}}},
                        "5": {"1": {"2": {"3": {}, "4": {}}, "3": {"2": {}, "3": {"2": 1}}}, "3": 1},
                    },
                    "8": {},
                    "9": {"2": {}, "3": {"1": {}, "2": {}}, "4": {"1": {"3": {"1": {"1": {"5": {"1": {}}, "6": {}}, "2": {}, "3": {"1": {"5": {"1": {}}, "6": {}}, "2": {}}}}, "4": {"1": {"2": {}}}}}},
                    "11": {"2": {}, "3": {}, "4": {"2": {"1": 1, "2": 2}}},
                    "12": {},
                    "14": {"2": {}, "3": {}, "4": {"2": {"1": 1, "2": 2}}},
                    "15": {"1": {}, "4": {}},
                    "17": {"1": {}, "4": {}},
                    "19": {"2": {}, "3": {}, "4": {"2": {"1": 1, "2": 2}}},
                    "22": {},
                    "23": {},
                },
                "2": {
                    "1": {
                        "2": {},
                        "3": {},
                        "4": {},
                        "5": {},
                        "6": {"1": {}, "2": {}, "3": {}, "4": {}, "5": {}, "7": {}},
                        "7": {},
                        "8": {},
                        "10": {},
                        "12": {},
                        "13": {"2": {}, "3": {}},
                        "15": {"1": {}},
                        "18": {},
                    },
                    "4": {"1": {}},
                    "9": {},
                    "11": {"1": {"1": {}, "4": {}, "5": {}, "6": {}, "9": {}}},
                    "14": {"1": {"1": {"1": {}, "2": {"2": {"1": {"1": {}}, "3": {}}}, "3": {"4": {"1": {"1": {}}, "3": {}}, "5": {"1": {"1": {}}, "3": {}}}}, "2": {}}},
                    "17": {},
                    "18": {"1": {}, "2": {"1": {}}},
                    "20": {"2": {"1": {}, "2": {}}},
                    "23": {},
                },
                "3": {
                    "2": {},
                    "3": {
                        "2": {},
                        "3": {},
                        "7": {},
                        "8": {},
                        "14": {"1": {}},
                        "16": {},
                        "17": {"2": {}},
                        "18": {},
                        "19": {},
                        "20": {},
                        "21": {},
                        "22": {},
                        "23": {},
                        "27": {"1": {}, "2": {"1": {}}},
                        "29": {},
                        "30": {},
                        "31": {},
                        "32": {},
                        "34": {},
                        "37": {},
                        "38": {},
                        "39": {},
                        "41": {},
                    },
                    "4": {"2": {}, "3": {}, "4": {}},
                    "7": {},
                    "12": {},
                    "13": {},
                    "14": {"1": {}, "2": {"1": {}, "2": {"1": {}}, "3": {}, "4": {"1": {}}}, "3": {"1": {}, "2": {"1": {}}, "3": {}, "4": {}}},
                    "15": {},
                    "16": {"1": {}},
                    "18": {},
                    "19": {"4": {"2": {}}, "6": {"2": {}, "3": {}}, "7": {"2": {}, "3": {}}, "8": {}},
                    "20": {},
                    "24": {},
                    "25": {},
                },
                "4": resume_token,
                "6": sync_token,
                "7": 2,
                "9": {"1": {"2": {"1": {}, "2": {}}}, "2": {"3": {"2": 1}}, "3": {"2": {}}, "4": {}, "7": {"1": {}}, "8": {"1": 2, "2": "\x01\x02\x03\x05\x06"}, "9": {}},
                "11": [1, 2],
                "12": {"2": {"1": {}, "2": {}}, "3": {"1": {}}, "4": {}},
                "13": {},
                "15": {"3": {"1": 1}},
                "18": {"169945741": {"1": {"1": {"4": [2, 1, 6, 8, 10, 15, 18, 13, 17, 19, 14, 20], "5": 6, "6": 2, "7": 1, "8": 2, "11": 3, "12": 1, "13": 3, "15": 1, "16": 1, "17": 1, "18": 2}}}},
                "19": {"1": {"1": {}, "2": {}}, "2": {"1": [1, 2, 4, 6, 5, 7]}, "3": {"1": {}, "2": {}}, "5": {"1": {}, "2": {}}, "6": {"1": {}}, "7": {"1": {}, "2": {}}, "8": {"1": {}}},
                "20": {
                    "1": 1,
                    "2": "AH_uQ41bEgartCAb9ZVh48fOzHLvaA7xJy_EHlv_4kR6Q7xI4Bol3igCVJ6HJ_VViRfrDrBJB5EQ",
                    "3": {
                        "1": "type.googleapis.com/photos.printing.client.PrintingPromotionSyncOptions",
                        "2": {"1": {"4": [2, 1, 6, 8, 10, 15, 18, 13, 17, 19, 14, 20], "5": 6, "6": 2, "7": 1, "8": 2, "11": 3, "12": 1, "13": 3, "15": 1, "16": 1, "17": 1, "18": 2}},
                    },
                },
                "21": {
                    "2": {"2": {}, "4": {}, "5": {}},
                    "3": {"2": {"1": 1}},
                    "5": {"1": {}},
                    "6": {"1": {}, "2": {"1": {}}},
                    "7": {"1": 2, "2": "\x01\x07\x08\t\n\r\x0e\x0f\x11\x13\x14\x16\x17-./01:\x06\x18267;>?@A89<", "3": "\x01"},
                    "8": {"3": {"1": {"1": {"2": {"1": 1}}}}, "4": {"1": {}}},
                    "9": {"1": {}},
                    "10": {"1": {"1": {}}, "3": {}, "5": {}, "6": {"1": {}}, "7": {}, "9": {}, "10": {}},
                    "11": {},
                    "12": {},
                    "13": {},
                },
                "22": {"1": 2},
                "25": {"1": {"1": {"1": {"1": {}}}}, "2": {}},
            },
            "2": {"1": {"1": {"1": {"1": {}}, "2": {}}}, "2": {}},
        }

        serialized_data = encode_message(proto_body, message_types.GET_LIB_PAGE)  # type: ignore

        with self._new_session() as session:
            response = session.post(
                "https://photosdata-pa.googleapis.com/6439526531001121323/18047484249733410717",
                headers=headers,
                data=serialized_data,
                timeout=self.timeout,
            )

        response.raise_for_status()

        decoded_message, _ = decode_message(response.content, message_type=message_types.LIB_STATE_RESPONSE_FIX)  # type: ignore
        return decoded_message

    def set_item_caption(self, dedup_key: str = "", caption: str = "") -> None:
        """Set item's caption

        Args:
            dedup_key: Target item's dedup key.
            caption: New caption.
        """

        headers = {
            "accept-encoding": "gzip",
            "Accept-Language": self.language,
            "content-type": "application/x-protobuf",
            "User-Agent": self.user_agent,
            "Authorization": f"Bearer {self.bearer_token}",
            "x-goog-ext-173412678-bin": "CgcIAhClARgC",
            "x-goog-ext-174067345-bin": "CgIIAg==",
        }

        proto_body = {"2": caption, "3": dedup_key}

        serialized_data = encode_message(proto_body, message_types.SET_CAPTION)  # type: ignore

        with self._new_session() as session:
            response = session.post(
                "https://photosdata-pa.googleapis.com/6439526531001121323/1552790390512470739",
                headers=headers,
                data=serialized_data,
                timeout=self.timeout,
            )

        response.raise_for_status()

    def get_thumbnail(
        self,
        media_key: str,
        width: int | None = None,
        height: int | None = None,
        force_jpeg: bool = True,
        content_version: int | None = None,
        no_overlay: bool = True,
        crop: bool = False,
    ) -> bytes:
        """Get media item's thumbnail

        Args:
            media_key: The unique identifier key for the media item.
            width: Optional; The desired width of the thumbnail in pixels.
            height: Optional; The desired height of the thumbnail in pixels.
            force_jpeg: If True, forces the response to be in JPEG format. Defaults to True.
            content_version: Specifies content version. Without it thumbnails will represent the original, not edited content.
            no_overlay: If True, removes overlay from the thumbnail, e.g. play symbol for videos. Defaults to True.
            crop: If True, crop the thumbnail. Defaults to False.

        Returns:
            bytes: Image bytes."""
        headers = {
            "authorization": f"Bearer {self.bearer_token}",
            "user-agent": self.user_agent,
            "accept-encoding": "gzip",
        }

        url = f"https://ap2.googleusercontent.com/gpa/{media_key}=k-sg"
        if width:
            url += f"-w{width}"
        if height:
            url += f"-h{height}"
        if force_jpeg:
            url += "-rj"
        if content_version:
            url += f"-iv{content_version}"
        if no_overlay:
            url += "-no"
        if crop:
            url += "-c"

        with self._new_session() as session:
            response = session.get(
                url,
                headers=headers,
                timeout=self.timeout,
            )

        response.raise_for_status()

        return response.content

    def set_favorite(self, dedup_key: str, is_favorite: bool) -> dict:
        """Sets or removes the favorite status for a single item.

        Args:
            dedup_key: Target item's dedup key.
            is_favorite: Whether to mark the item as favorite (True) or remove favorite status (False).

        Returns:
            dict: Decoded api response.
        """
        headers = {
            "accept-encoding": "gzip",
            "Accept-Language": self.language,
            "content-type": "application/x-protobuf",
            "User-Agent": self.user_agent,
            "Authorization": f"Bearer {self.bearer_token}",
            "x-goog-ext-173412678-bin": "CgcIAhClARgC",
            "x-goog-ext-174067345-bin": "CgIIAg==",
        }

        action_map = {True: 1, False: 2}

        proto_body = {"1": {"2": dedup_key}, "2": {"1": action_map[is_favorite]}, "3": {"1": {"19": {}}}}

        serialized_data = encode_message(proto_body, message_types.SET_FAVORITE)  # type: ignore

        with self._new_session() as session:
            response = session.post(
                "https://photosdata-pa.googleapis.com/6439526531001121323/5144645502632292153",
                headers=headers,
                data=serialized_data,
                timeout=self.timeout,
            )

        response.raise_for_status()

        decoded_message, _ = decode_message(response.content)
        return decoded_message

    def set_archived(self, dedup_keys: Sequence[str], is_archived: bool) -> dict:
        """Sets or removes the archived status for multiple items.

        Args:
            dedup_keys: Sequence of target items' dedup keys.
            is_archived: Whether to mark the item as archived (True) or remove archived status (False).

        Returns:
            dict: Decoded api response.
        """
        headers = {
            "accept-encoding": "gzip",
            "Accept-Language": self.language,
            "content-type": "application/x-protobuf",
            "User-Agent": self.user_agent,
            "Authorization": f"Bearer {self.bearer_token}",
            "x-goog-ext-173412678-bin": "CgcIAhClARgC",
            "x-goog-ext-174067345-bin": "CgIIAg==",
        }

        action_map = {True: 1, False: 2}

        proto_body = {
            "1": [{"1": key, "2": {"1": action_map[is_archived]}} for key in dedup_keys],
            "3": 1,
        }

        serialized_data = encode_message(proto_body, message_types.SET_ARCHIVED)  # type: ignore

        with self._new_session() as session:
            response = session.post(
                "https://photosdata-pa.googleapis.com/6439526531001121323/6715446385130606868",
                headers=headers,
                data=serialized_data,
                timeout=self.timeout,
            )

        response.raise_for_status()

        decoded_message, _ = decode_message(response.content)
        return decoded_message

    def get_download_urls(self, media_key: str) -> dict:
        """Get item's download links.

        Args:
            media_key: Target item's media key.

        Returns:
            dict: Decoded api response.

        Note:
            output_dict["1"]["5"]["2"]["5"] - url for downloading the file with applied edits (if any)
            output_dict["1"]["5"]["2"]["6"] - url for downloading the original file
        """
        headers = {
            "accept-encoding": "gzip",
            "Accept-Language": self.language,
            "content-type": "application/x-protobuf",
            "User-Agent": self.user_agent,
            "Authorization": f"Bearer {self.bearer_token}",
            "x-goog-ext-173412678-bin": "CgcIAhClARgC",
            "x-goog-ext-174067345-bin": "CgIIAg==",
        }

        proto_body = {"1": {"1": {"1": media_key}}, "2": {"1": {"7": {"2": {}}}, "5": {"2": {}, "3": {}, "5": {"1": {}, "3": 0}}}}

        serialized_data = encode_message(proto_body, message_types.GET_DOWNLOAD_URLS)  # type: ignore

        with self._new_session() as session:
            response = session.post(
                "https://photosdata-pa.googleapis.com/$rpc/social.frontend.photos.preparedownloaddata.v1.PhotosPrepareDownloadDataService/PhotosPrepareDownload",
                headers=headers,
                data=serialized_data,
                timeout=self.timeout,
            )

        response.raise_for_status()

        decoded_message, _ = decode_message(response.content)
        return decoded_message

    def restore_from_trash(self, dedup_keys: Sequence[str]) -> dict:
        """Restore items from trash.

        Args:
            dedup_keys: Sequence of target items' dedup keys.

        Returns:
            dict: Decoded api response.
        """
        headers = {
            "accept-encoding": "gzip",
            "Accept-Language": self.language,
            "content-type": "application/x-protobuf",
            "User-Agent": self.user_agent,
            "Authorization": f"Bearer {self.bearer_token}",
            "x-goog-ext-173412678-bin": "CgcIAhClARgC",
            "x-goog-ext-174067345-bin": "CgIIAg==",
        }

        proto_body = {
            "2": 3,
            "3": dedup_keys,
            "4": 2,
            "8": {"4": {"2": {}, "3": {"1": {}}}},
            "9": {"1": 5, "2": {"1": self.client_version_code, "2": str(self.android_api_version)}},
        }

        serialized_data = encode_message(proto_body, message_types.RESTORE_FROM_TRASH)  # type: ignore

        with self._new_session() as session:
            response = session.post(
                "https://photosdata-pa.googleapis.com/6439526531001121323/17490284929287180316",
                headers=headers,
                data=serialized_data,
                timeout=self.timeout,
            )

        response.raise_for_status()

        decoded_message, _ = decode_message(response.content)
        return decoded_message

    def get_stream_manifest(self, media_key: str, protocol: Literal["hls", "dash"] = "hls", content_version: int | None = None) -> str:
        """
        Fetch the streaming manifest for a given media key.

        Args:
            media_key: Target video item's media key.
            protocol: The streaming protocol format to request. Must be either
                'hls' or 'dash'. Defaults to 'hls' if not specified.
            content_version: Specifies content version. Without it manifest will represent the original, not edited content.

        Returns:
            str: The streaming manifest content.
        """
        headers = {
            "accept-encoding": "gzip",
            "User-Agent": self.user_agent,
            "Authorization": f"Bearer {self.bearer_token}",
        }

        url = f"https://lh3.googleusercontent.com/p/{media_key}%3Dmm%2C{protocol}-vm"

        if content_version:
            url += f"-iv{content_version}"

        with self._new_session() as session:
            response = session.post(
                url,
                headers=headers,
                timeout=self.timeout,
            )

        response.raise_for_status()
        return response.text
