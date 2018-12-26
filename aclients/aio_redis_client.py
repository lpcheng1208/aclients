#!/usr/bin/env python3
# coding=utf-8

"""
@author: guoyanfeng
@software: PyCharm
@time: 18-12-25 下午5:15
"""
import ujson
import uuid
from collections import MutableMapping

import aelog
import aredis
from aredis import RedisError

from aclients.exceptions import RedisClientError
from aclients.utils import ignore_error

__all__ = ("Session", "AIORedisClient")

EXPIRED = 24 * 60 * 60


class Session(object):
    """
    保存实际看结果的session实例
    Args:

    """

    def __init__(self, user_id, *, session_id=None, org_id=None, permission_id=None, **kwargs):
        self.user_id = user_id
        self.session_id = session_id or uuid.uuid4().hex
        self.org_id = org_id or uuid.uuid4().hex
        self.permission_id = permission_id or uuid.uuid4().hex
        for k, v in kwargs.items():
            setattr(self, k, v)


class AIORedisClient(object):
    """
    redis 非阻塞工具类
    """

    def __init__(self, app=None, *, host="127.0.0.1", port=6379, dbname=0, passwd="", pool_size=50, **kwargs):
        """
        redis 非阻塞工具类
        Args:
            app: app应用
            host:redis host
            port:redis port
            dbname: database name
            passwd: redis password
            pool_size: redis pool size
        """
        self.pool = None
        self.redis_db = None

        if app is not None:
            self.init_app(app, host=host, port=port, dbname=dbname, passwd=passwd, pool_size=pool_size)

    def init_app(self, app, *, host="127.0.0.1", port=6379, dbname=None, passwd="", pool_size=50):
        """
        redis 非阻塞工具类
        Args:
            app: app应用
            host:redis host
            port:redis port
            dbname: database name
            passwd: redis password
            pool_size: redis pool size
        Returns:

        """
        host = app.config.get("ACLIENTS_REDIS_HOST", None) or host
        port = app.config.get("ACLIENTS_REDIS_PORT", None) or port
        dbname = app.config.get("ACLIENTS_REDIS_DBNAME", None) or dbname
        passwd = app.config.get("ACLIENTS_REDIS_PASSWD", None) or passwd
        pool_size = app.config.get("ACLIENTS_REDIS_POOL_SIZE", None) or pool_size

        @app.listener('before_server_start')
        def open_connection():
            """

            Args:

            Returns:

            """
            # 返回值都做了解码，应用层不需要再decode
            self.pool = aredis.ConnectionPool(host=host, port=port, db=dbname, password=passwd, decode_responses=True,
                                              max_connections=pool_size)
            self.redis_db = aredis.StrictRedis(connection_pool=self.pool, decode_responses=True)

        @app.listener('after_server_stop')
        def close_connection():
            """
            释放redis连接池所有连接
            Args:

            Returns:

            """
            self.redis_db = None
            self.pool.disconnect()

    async def save_session(self, session: Session, ex=EXPIRED):
        """
        利用hash map保存session
        Args:
            session: Session 实例
            ex: 过期时间，单位秒
        Returns:

        """
        session_data = {key: val if isinstance(val, str) else ujson.dumps(val) for key, val in vars(session).items()}

        try:
            if not await self.redis_db.hmset(session_data["session_id"], session_data):
                raise RedisClientError("save session failed, session_id={}".format(session_data["session_id"]))
            if not await self.redis_db.expire(session_data["session_id"], ex):
                raise RedisClientError("set session expire failed, session_id={}".format(session_data["session_id"]))
        except RedisError as e:
            aelog.exception("save session error: {}, {}".format(session.session_id, e))
            raise RedisClientError(str(e))
        else:
            return session.session_id

    async def delete_session(self, session_id):
        """
        利用hash map删除session
        Args:
            session_id: session id
        Returns:

        """

        try:
            session_id_ = await self.redis_db.hget(session_id, "session_id")
            if session_id_ != session_id:
                raise RedisClientError("invalid session_id, session_id={}".format(session_id))

            if not await self.redis_db.delete(session_id):
                raise RedisClientError("delete session failed, session_id={}".format(session_id))
        except RedisError as e:
            aelog.exception("delete session error: {}, {}".format(session_id, e))
            raise RedisClientError(str(e))

    async def update_session(self, session: Session, ex=EXPIRED):
        """
        利用hash map更新session
        Args:
            session: Session实例
            ex: 过期时间，单位秒
        Returns:

        """
        session_data = {key: val if isinstance(val, str) else ujson.dumps(val) for key, val in vars(session).items()}

        try:
            if not await self.redis_db.hmset(session_data["session_id"], session_data):
                raise RedisClientError("update session failed, session_id={}".format(session_data["session_id"]))
            if not await self.redis_db.expire(session_data["session_id"], ex):
                raise RedisClientError("set session expire failed, session_id={}".format(session_data["session_id"]))
        except RedisError as e:
            aelog.exception("update session error: {}, {}".format(session_data["session_id"], e))
            raise RedisClientError(str(e))

    async def get_session(self, session_id, ex=EXPIRED) -> Session:
        """
        获取session
        Args:
            session_id: session id
            ex: 过期时间，单位秒
        Returns:

        """

        try:
            session_data = await self.redis_db.hgetall(session_id)
            if not session_data:
                raise RedisClientError("not found session, session_id={}".format(session_id))

            if not await self.redis_db.expire(session_id, ex):
                raise RedisClientError("set session expire failed, session_id={}".format(session_id))
        except RedisError as e:
            aelog.exception("get session error: {}, {}".format(session_id, e))
            raise RedisClientError(e)
        else:
            session_data = {key: val if isinstance(val, str) else ujson.loads(val) for key, val in session_data.items()}
            return Session(user_id=session_data.pop('user_id'), session_id=session_data["session_id"],
                           org_id=session_data["org_id"], permission_id=session_data["permission_id"], **session_data)

    async def verify(self, session_id):
        """
        校验session，主要用于登录校验
        Args:
            session_id
        Returns:

        """
        try:
            session = await self.get_session(session_id)
        except RedisClientError as e:
            raise RedisClientError(str(e))
        else:
            #  这一步按照现有的逻辑是多余的，不过可以暂时保留
            if session_id != session.session_id:
                raise RedisClientError("invalid session_id, session_id={}".format(session_id))
            return session

    async def save_update_hash_data(self, name, hash_data: dict, ex=EXPIRED):
        """
        获取hash对象field_name对应的值
        Args:
            name: redis hash key的名称
            hash_data: 获取的hash对象中属性的名称
            ex: 过期时间，单位秒
        Returns:
            反序列化对象
        """
        if not isinstance(hash_data, MutableMapping):
            raise ValueError("hash data error, must be MutableMapping.")
        try:
            if not await self.redis_db.hmset(name, hash_data):
                raise RedisClientError("save hash data failed, session_id={}".format(name))
            if not await self.redis_db.expire(name, ex):
                raise RedisClientError("set hash data expire failed, session_id={}".format(name))
        except RedisError as e:
            raise RedisClientError(str(e))
        else:
            return name

    async def get_hash_data(self, name, field_name=None, ex=EXPIRED):
        """
        获取hash对象field_name对应的值
        Args:
            name: redis hash key的名称
            field_name: 获取的hash对象中属性的名称
            ex: 过期时间，单位秒
        Returns:
            反序列化对象
        """
        try:
            if field_name:
                data = await self.redis_db.hget(name, field_name)
            else:
                data = await self.redis_db.hgetall(name)

            if not await self.redis_db.expire(name, ex):
                raise RedisClientError("set expire failed, name={}".format(name))
        except RedisError as e:
            raise RedisClientError(str(e))
        else:
            return data

    async def get_list_data(self, name, start=0, end=-1, ex=EXPIRED):
        """
        保存数据到redis的列表中
        Args:
            name: redis key的名称
            start: 获取数据的起始位置,默认列表的第一个值
            end: 获取数据的结束位置，默认列表的最后一个值
            ex: 过期时间，单位秒
        Returns:

        """
        try:
            data = await self.redis_db.lrange(name, start=start, end=end)
            if not await self.redis_db.expire(name, ex):
                raise RedisClientError("set expire failed, name={}".format(name))
        except RedisError as e:
            raise RedisClientError(str(e))
        else:
            return data

    async def save_list_data(self, name, list_data: list or str, save_to_left=True, ex=EXPIRED):
        """
        保存数据到redis的列表中
        Args:
            name: redis key的名称
            list_data: 保存的值,可以是单个值也可以是元祖
            save_to_left: 是否保存到列表的左边，默认保存到左边
            ex: 过期时间，单位秒
        Returns:

        """
        list_data = (list_data,) if isinstance(list_data, str) else list_data
        try:
            if save_to_left:
                if not await self.redis_db.lpush(name, *list_data):
                    raise RedisClientError("lpush value to head failed.")
            else:
                if not await self.redis_db.rpush(name, *list_data):
                    raise RedisClientError("lpush value to tail failed.")
            if not await self.redis_db.expire(name, ex):
                raise RedisClientError("set expire failed, name={}".format(name))
        except RedisError as e:
            raise RedisClientError(str(e))
        else:
            return name

    async def save_update_usual_data(self, name, value, ex=EXPIRED):
        """
        保存列表、映射对象为普通的字符串
        Args:
            name: redis key的名称
            value: 保存的值，可以是可序列化的任何职
            ex: 过期时间，单位秒
        Returns:

        """
        value = ujson.dumps(value) if not isinstance(value, str) else value
        try:
            if not await self.redis_db.set(name, value, ex):
                raise RedisClientError("set serializable value failed!")
        except RedisError as e:
            raise RedisClientError(str(e))
        else:
            return name

    async def get_usual_data(self, name, ex=EXPIRED):
        """
        获取name对应的值
        Args:
            name: redis key的名称
            ex: 过期时间，单位秒
        Returns:
            反序列化对象
        """
        try:
            data = await self.redis_db.get(name)
            if not await self.redis_db.expire(name, ex):
                raise RedisClientError("set expire failed, name={}".format(name))

        except RedisError as e:
            raise RedisClientError(str(e))
        else:
            with ignore_error():
                data = ujson.loads(data)
            return data

    async def is_exist_key(self, name):
        """
        判断redis key是否存在
        Args:
            name: redis key的名称
        Returns:

        """
        try:
            data = await self.redis_db.exists(name)
        except RedisError as e:
            raise RedisClientError(str(e))
        else:
            return data

    async def delete_keys(self, names: list):
        """
        删除一个或多个redis key
        Args:
            names: redis key的名称
        Returns:

        """
        names = (names,) if isinstance(names, str) else names
        try:
            if not await self.redis_db.delete(*names):
                raise RedisClientError("Delete redis keys failed {}.".format(*names))
        except RedisError as e:
            raise RedisClientError(str(e))

    async def get_keys(self, pattern_name):
        """
        根据正则表达式获取redis的keys
        Args:
            pattern_name:正则表达式的名称
        Returns:

        """
        try:
            data = await self.redis_db.keys(pattern_name)
        except RedisError as e:
            raise RedisClientError(str(e))
        else:
            return data