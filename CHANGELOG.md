# Changelog

## [0.3.6](https://github.com/a2aproject/a2a-python/compare/v0.3.5...v0.3.6) (2025-09-09)


### Features

* add JSON-RPC `method` to `ServerCallContext.state` ([d62df7a](https://github.com/a2aproject/a2a-python/commit/d62df7a77e556f26556fc798a55dc6dacec21ea4))
* **gRPC:** Add proto conversion utilities ([80fc33a](https://github.com/a2aproject/a2a-python/commit/80fc33aaef647826208d9020ef70e5e6592468e3))

## [0.3.5](https://github.com/a2aproject/a2a-python/compare/v0.3.4...v0.3.5) (2025-09-08)


### Bug Fixes

* Prevent client disconnect from stopping task execution ([#440](https://github.com/a2aproject/a2a-python/issues/440)) ([58b4c81](https://github.com/a2aproject/a2a-python/commit/58b4c81746fc83e65f23f46308c47099697554ea)), closes [#296](https://github.com/a2aproject/a2a-python/issues/296)
* **proto:** Adds metadata field to A2A DataPart proto ([#455](https://github.com/a2aproject/a2a-python/issues/455)) ([6d0ef59](https://github.com/a2aproject/a2a-python/commit/6d0ef593adaa22b2af0a5dd1a186646c180e3f8c))


### Documentation

* add example docs for `[@validate](https://github.com/validate)` and `[@validate](https://github.com/validate)_async_generator` ([#422](https://github.com/a2aproject/a2a-python/issues/422)) ([18289eb](https://github.com/a2aproject/a2a-python/commit/18289eb19bbdaebe5e36e26be686e698f223160b))
* Restructure README ([9758f78](https://github.com/a2aproject/a2a-python/commit/9758f7896c5497d6ca49f798296a7380b2134b29))

## [0.3.4](https://github.com/a2aproject/a2a-python/compare/v0.3.3...v0.3.4) (2025-09-02)


### Features

* Add `ServerCallContext` into task store operations ([#443](https://github.com/a2aproject/a2a-python/issues/443)) ([e3e5c4b](https://github.com/a2aproject/a2a-python/commit/e3e5c4b7dcb5106e943b9aeb8e761ed23cc166a2))
* Add extensions support to `TaskUpdater.add_artifact` ([#436](https://github.com/a2aproject/a2a-python/issues/436)) ([598d8a1](https://github.com/a2aproject/a2a-python/commit/598d8a10e61be83bcb7bc9377365f7c42bc6af41))


### Bug Fixes

* convert auth_required state in proto utils ([#444](https://github.com/a2aproject/a2a-python/issues/444)) ([ac12f05](https://github.com/a2aproject/a2a-python/commit/ac12f0527d923800192c47dc1bd2e7eed262dfe6))
* handle concurrent task completion during cancellation ([#449](https://github.com/a2aproject/a2a-python/issues/449)) ([f4c9c18](https://github.com/a2aproject/a2a-python/commit/f4c9c18cfef3ccab1ac7bb30cc7f8293cf3e3ef6))
* Remove logger error from init on `rest_adapter` and `jsonrpc_app` ([#439](https://github.com/a2aproject/a2a-python/issues/439)) ([9193208](https://github.com/a2aproject/a2a-python/commit/9193208aabac2655a197732ff826e3c2d76f11b5))
* resolve streaming endpoint deadlock by pre-consuming request body ([#426](https://github.com/a2aproject/a2a-python/issues/426)) ([4186731](https://github.com/a2aproject/a2a-python/commit/4186731df60f7adfcd25f19078d055aca26612a3))
* Sync jsonrpc and rest implementation of authenticated agent card ([#441](https://github.com/a2aproject/a2a-python/issues/441)) ([9da9ecc](https://github.com/a2aproject/a2a-python/commit/9da9ecc96856a2474d75f986a1f45488c36f53e3))


### Performance Improvements

* Improve performance and code style for `proto_utils.py` ([#452](https://github.com/a2aproject/a2a-python/issues/452)) ([1e4b574](https://github.com/a2aproject/a2a-python/commit/1e4b57457386875b64362113356c615bc87315e3))

## [0.3.3](https://github.com/a2aproject/a2a-python/compare/v0.3.2...v0.3.3) (2025-08-22)


### Features

* Update proto conversion utilities ([#424](https://github.com/a2aproject/a2a-python/issues/424)) ([a3e7e1e](https://github.com/a2aproject/a2a-python/commit/a3e7e1ef2684f979a3b8cbde1f9fd24ce9154e40))


### Bug Fixes

* fixing JSONRPC error mapping ([#414](https://github.com/a2aproject/a2a-python/issues/414)) ([d2e869f](https://github.com/a2aproject/a2a-python/commit/d2e869f567a84f59967cf59a044d6ca1e0d00daf))
* Revert code that enforces uuid structure on context id in tasks ([#429](https://github.com/a2aproject/a2a-python/issues/429)) ([e3a7207](https://github.com/a2aproject/a2a-python/commit/e3a7207164503f64900feaa4ef470d37fb2bb145)), closes [#427](https://github.com/a2aproject/a2a-python/issues/427)


### Performance Improvements

* Optimize logging performance and modernize string formatting ([#411](https://github.com/a2aproject/a2a-python/issues/411)) ([3ffae8f](https://github.com/a2aproject/a2a-python/commit/3ffae8f8046aef20e559e19c21a5f9464a2c89ca))


### Reverts

* Revert "chore(gRPC): Update a2a.proto to include metadata on GetTaskRequest" ([#428](https://github.com/a2aproject/a2a-python/issues/428)) ([39c6b43](https://github.com/a2aproject/a2a-python/commit/39c6b430c6b57e84255f56894dcc46a740a53f9b))

## [0.3.2](https://github.com/a2aproject/a2a-python/compare/v0.3.1...v0.3.2) (2025-08-20)


### Bug Fixes

* Add missing mime_type and name in proto conversion utils ([#408](https://github.com/a2aproject/a2a-python/issues/408)) ([72b2ee7](https://github.com/a2aproject/a2a-python/commit/72b2ee75dccfc8399edaa0837a025455b4b53a17))
* Add name field to FilePart protobuf message ([#403](https://github.com/a2aproject/a2a-python/issues/403)) ([1dbe33d](https://github.com/a2aproject/a2a-python/commit/1dbe33d5cf2c74019b72c709f3427aeba54bf4e3))
* Client hangs when implementing `AgentExecutor` and `await`ing twice in execute method ([#379](https://github.com/a2aproject/a2a-python/issues/379)) ([c147a83](https://github.com/a2aproject/a2a-python/commit/c147a83d3098e5ab2cd5b695a3bd71e17bf13b4c))
* **grpc:** Update `CreateTaskPushNotificationConfig` endpoint to `/v1/{parent=tasks/*/pushNotificationConfigs}` ([#415](https://github.com/a2aproject/a2a-python/issues/415)) ([73dddc3](https://github.com/a2aproject/a2a-python/commit/73dddc3a3dc0b073d5559b3d0ec18ff4d20b6f7d))
* make `event_consumer` tolerant to closed queues on py3.13 ([#407](https://github.com/a2aproject/a2a-python/issues/407)) ([a371461](https://github.com/a2aproject/a2a-python/commit/a371461c3b77aa9643c3a3378bb4405356863bff))
* non-blocking `send_message` server handler not invoke push notification ([#394](https://github.com/a2aproject/a2a-python/issues/394)) ([db82a65](https://github.com/a2aproject/a2a-python/commit/db82a6582821a37aa8033d7db426557909ab10c6))
* **proto:** Add `icon_url` to `a2a.proto` ([#416](https://github.com/a2aproject/a2a-python/issues/416)) ([00703e3](https://github.com/a2aproject/a2a-python/commit/00703e3df45ea7708613791ec35e843591333eca))
* **spec:** Suggest Unique Identifier fields to be UUID ([#405](https://github.com/a2aproject/a2a-python/issues/405)) ([da14cea](https://github.com/a2aproject/a2a-python/commit/da14cea950f1af486e7891fa49199249d29b6f37))

## [0.3.1](https://github.com/a2aproject/a2a-python/compare/v0.3.0...v0.3.1) (2025-08-13)


### Features

* Add agent card as a route in rest adapter ([ba93053](https://github.com/a2aproject/a2a-python/commit/ba93053850a767a8959bc634883008fcc1366e09))


### Bug Fixes

* gracefully handle task exceptions in event consumer ([#383](https://github.com/a2aproject/a2a-python/issues/383)) ([2508a9b](https://github.com/a2aproject/a2a-python/commit/2508a9b8ec1a1bfdc61e9012b7d68b33082b3981))
* openapi working in sub-app ([#324](https://github.com/a2aproject/a2a-python/issues/324)) ([dec4b48](https://github.com/a2aproject/a2a-python/commit/dec4b487514db6cbb25f0c6fa7e1275a1ab0ba71))
* Pass `message_length` param in `get_task()` ([#384](https://github.com/a2aproject/a2a-python/issues/384)) ([b6796b9](https://github.com/a2aproject/a2a-python/commit/b6796b9e1432ef8499eff454f869edf4427fd704))
* relax protobuf dependency version requirement ([#381](https://github.com/a2aproject/a2a-python/issues/381)) ([0f55f55](https://github.com/a2aproject/a2a-python/commit/0f55f554ba9f6bf53fa3d9a91f66939f36e1ef2e))
* Use HasField for simple message retrieval for grpc transport ([#380](https://github.com/a2aproject/a2a-python/issues/380)) ([3032aa6](https://github.com/a2aproject/a2a-python/commit/3032aa660f6f3b72dc7dd8b49b0e2f4d432c7a22))

## [0.3.0](https://github.com/a2aproject/a2a-python/compare/v0.2.16...v0.3.0) (2025-07-31)


### ⚠ BREAKING CHANGES

* **deps:** Make opentelemetry an optional dependency ([#369](https://github.com/a2aproject/a2a-python/issues/369))
* **spec:** Update Agent Card Well-Known Path to `/.well-known/agent-card.json` ([#320](https://github.com/a2aproject/a2a-python/issues/320))
* Remove custom `__getattr__` and `__setattr__` for `camelCase` fields in `types.py` ([#335](https://github.com/a2aproject/a2a-python/issues/335))
  * Use Script [`refactor_camel_to_snake.sh`](https://github.com/a2aproject/a2a-samples/blob/main/samples/python/refactor_camel_to_snake.sh) to convert your codebase to the new field names.
* Add mTLS to SecuritySchemes, add oauth2 metadata url field, allow Skills to specify Security ([#362](https://github.com/a2aproject/a2a-python/issues/362))
* Support for serving agent card at deprecated path ([#352](https://github.com/a2aproject/a2a-python/issues/352))

### Features

* Add `metadata` as parameter to `TaskUpdater.update_status()` ([#371](https://github.com/a2aproject/a2a-python/issues/371)) ([9444ed6](https://github.com/a2aproject/a2a-python/commit/9444ed629b925e285cd08aae3078ccd8b9bda6f2))
* Add mTLS to SecuritySchemes, add oauth2 metadata url field, allow Skills to specify Security ([#362](https://github.com/a2aproject/a2a-python/issues/362)) ([be6c517](https://github.com/a2aproject/a2a-python/commit/be6c517e1f2db50a9217de91a9080810c36a7a1b))
* Add RESTful API Serving ([#348](https://github.com/a2aproject/a2a-python/issues/348)) ([82a6b7c](https://github.com/a2aproject/a2a-python/commit/82a6b7cc9b83484a4ceabc2323e14e2ff0270f87))
* Add server-side support for plumbing requested and activated extensions ([#333](https://github.com/a2aproject/a2a-python/issues/333)) ([4d5b92c](https://github.com/a2aproject/a2a-python/commit/4d5b92c61747edcabcfd825256a5339bb66c3e91))
* Allow agent cards (default and extended) to be dynamic ([#365](https://github.com/a2aproject/a2a-python/issues/365)) ([ee92aab](https://github.com/a2aproject/a2a-python/commit/ee92aabe1f0babbba2fdbdefe21f2dbe7a899077))
* Support for serving agent card at deprecated path ([#352](https://github.com/a2aproject/a2a-python/issues/352)) ([2444034](https://github.com/a2aproject/a2a-python/commit/2444034b7aa1d1af12bedecf40f27dafc4efec95))
* support non-blocking `sendMessage` ([#349](https://github.com/a2aproject/a2a-python/issues/349)) ([70b4999](https://github.com/a2aproject/a2a-python/commit/70b499975f0811c8055ebd674bcb4070805506d4))
* Type update to support fetching extended card ([#361](https://github.com/a2aproject/a2a-python/issues/361)) ([83304bb](https://github.com/a2aproject/a2a-python/commit/83304bb669403b51607973c1a965358d2e8f6ab0))


### Bug Fixes

* Add Input Validation for Task Context IDs in new_task Function ([#340](https://github.com/a2aproject/a2a-python/issues/340)) ([a7ed7ef](https://github.com/a2aproject/a2a-python/commit/a7ed7efed8fcdcc556616a5fc1cb8f968a116733))
* **deps:** Reduce FastAPI library required version to `0.95.0` ([#372](https://github.com/a2aproject/a2a-python/issues/372)) ([a319334](https://github.com/a2aproject/a2a-python/commit/a31933456e08929f665ccec57ac07b8b9118990d))
* Remove `DeprecationWarning` for regular properties ([#345](https://github.com/a2aproject/a2a-python/issues/345)) ([2806f3e](https://github.com/a2aproject/a2a-python/commit/2806f3eb7e1293924bb8637fd9c2cfe855858592))
* **spec:** Add `SendMessageRequest.request` `json_name` mapping to `message` proto ([bc97cba](https://github.com/a2aproject/a2a-python/commit/bc97cba5945a49bea808feb2b1dc9eeb30007599))
* **spec:** Add Transport enum to specification (https://github.com/a2aproject/A2A/pull/909) ([d9e463c](https://github.com/a2aproject/a2a-python/commit/d9e463cf1f8fbe486d37da3dd9009a19fe874ff0))


### Documentation

* Address typos in docstrings and docs. ([#370](https://github.com/a2aproject/a2a-python/issues/370)) ([ee48d68](https://github.com/a2aproject/a2a-python/commit/ee48d68d6c42a2a0c78f8a4666d1aded1a362e78))


### Miscellaneous Chores

* Add support for authenticated extended card method ([#356](https://github.com/a2aproject/a2a-python/issues/356)) ([b567e80](https://github.com/a2aproject/a2a-python/commit/b567e80735ae7e75f0bdb22f025b97895ce3b0dd))


### Code Refactoring

* **deps:** Make opentelemetry an optional dependency ([#369](https://github.com/a2aproject/a2a-python/issues/369)) ([9ad8b96](https://github.com/a2aproject/a2a-python/commit/9ad8b9623ffdc074ec561cbe65cfc2a2ba38bd0b))
* Remove custom `__getattr__` and `__setattr__` for `camelCase` fields in `types.py` ([#335](https://github.com/a2aproject/a2a-python/issues/335)) ([cd94167](https://github.com/a2aproject/a2a-python/commit/cd941675d10868922adf14266901d035516a31cf))
* **spec:** Update Agent Card Well-Known Path to `/.well-known/agent-card.json` ([#320](https://github.com/a2aproject/a2a-python/issues/320)) ([270ea9b](https://github.com/a2aproject/a2a-python/commit/270ea9b0822b689e50ed12f745a24a17e7917e73))

## [0.2.16](https://github.com/a2aproject/a2a-python/compare/v0.2.15...v0.2.16) (2025-07-21)


### Features

* Convert fields in `types.py` to use `snake_case` ([#199](https://github.com/a2aproject/a2a-python/issues/199)) ([0bb5563](https://github.com/a2aproject/a2a-python/commit/0bb55633272605a0404fc14c448a9dcaca7bb693))


### Bug Fixes

* Add deprecation warning for camelCase alias ([#334](https://github.com/a2aproject/a2a-python/issues/334)) ([f22b384](https://github.com/a2aproject/a2a-python/commit/f22b384d919e349be8d275c8f44bd760d627bcb9))
* client should not specify `taskId` if it doesn't exist ([#264](https://github.com/a2aproject/a2a-python/issues/264)) ([97f1093](https://github.com/a2aproject/a2a-python/commit/97f109326c7fe291c96bb51935ac80e0fab4cf66))

## [0.2.15](https://github.com/a2aproject/a2a-python/compare/v0.2.14...v0.2.15) (2025-07-21)


### Bug Fixes

* Add Input Validation for Empty Message Content ([#327](https://github.com/a2aproject/a2a-python/issues/327)) ([5061834](https://github.com/a2aproject/a2a-python/commit/5061834e112a4eb523ac505f9176fc42d86d8178))
* Prevent import grpc issues for Client after making dependencies optional ([#330](https://github.com/a2aproject/a2a-python/issues/330)) ([53ad485](https://github.com/a2aproject/a2a-python/commit/53ad48530b47ef1cbd3f40d0432f9170b663839d)), closes [#326](https://github.com/a2aproject/a2a-python/issues/326)

## [0.2.14](https://github.com/a2aproject/a2a-python/compare/v0.2.13...v0.2.14) (2025-07-18)


### Features

* Set grpc dependencies as optional ([#322](https://github.com/a2aproject/a2a-python/issues/322)) ([365f158](https://github.com/a2aproject/a2a-python/commit/365f158f87166838b55bdadd48778cb313a453e1))
* **spec:** Update A2A types from specification 🤖 ([#325](https://github.com/a2aproject/a2a-python/issues/325)) ([02e7a31](https://github.com/a2aproject/a2a-python/commit/02e7a3100e000e115b4aeec7147cf8fc1948c107))

## [0.2.13](https://github.com/a2aproject/a2a-python/compare/v0.2.12...v0.2.13) (2025-07-17)


### Features

* Add `get_data_parts()` and `get_file_parts()` helper methods ([#312](https://github.com/a2aproject/a2a-python/issues/312)) ([5b98c32](https://github.com/a2aproject/a2a-python/commit/5b98c3240db4ff6007e242742f76822fc6ea380c))
* Support for Database based Push Config Store ([#299](https://github.com/a2aproject/a2a-python/issues/299)) ([e5d99ee](https://github.com/a2aproject/a2a-python/commit/e5d99ee9e478cda5e93355cba2e93f1d28039806))
* Update A2A types from specification 🤖 ([#319](https://github.com/a2aproject/a2a-python/issues/319)) ([18506a4](https://github.com/a2aproject/a2a-python/commit/18506a4fe32c1956725d8f205ec7848f7b86c77d))


### Bug Fixes

* Add Input Validation for Task IDs in TaskManager ([#310](https://github.com/a2aproject/a2a-python/issues/310)) ([a38d438](https://github.com/a2aproject/a2a-python/commit/a38d43881d8476e6fbcb9766b59e3378dbe64306))
* Add validation for empty artifact lists in `completed_task` ([#308](https://github.com/a2aproject/a2a-python/issues/308)) ([c4a324d](https://github.com/a2aproject/a2a-python/commit/c4a324dcb693f19fbbf90cee483f6a912698a921))
* Handle readtimeout errors. ([#305](https://github.com/a2aproject/a2a-python/issues/305)) ([b94b8f5](https://github.com/a2aproject/a2a-python/commit/b94b8f52bf58315f3ef138b6a1ffaf894f35bcef)), closes [#249](https://github.com/a2aproject/a2a-python/issues/249)


### Documentation

* Update Documentation Site Link ([#315](https://github.com/a2aproject/a2a-python/issues/315)) ([edf392c](https://github.com/a2aproject/a2a-python/commit/edf392cfe531d0448659e2f08ab08f0ba05475b3))

## [0.2.12](https://github.com/a2aproject/a2a-python/compare/v0.2.11...v0.2.12) (2025-07-14)


### Features

* add `metadata` property to `RequestContext` ([#302](https://github.com/a2aproject/a2a-python/issues/302)) ([e781ced](https://github.com/a2aproject/a2a-python/commit/e781ced3b082ef085f9aeef02ceebb9b35c68280))
* add A2ABaseModel ([#292](https://github.com/a2aproject/a2a-python/issues/292)) ([24f2eb0](https://github.com/a2aproject/a2a-python/commit/24f2eb0947112539cbd4e493c98d0d9dadc87f05))
* add support for notification tokens in PushNotificationSender ([#266](https://github.com/a2aproject/a2a-python/issues/266)) ([75aa4ed](https://github.com/a2aproject/a2a-python/commit/75aa4ed866a6b4005e59eb000e965fb593e0888f))
* Update A2A types from specification 🤖 ([#289](https://github.com/a2aproject/a2a-python/issues/289)) ([ecb321a](https://github.com/a2aproject/a2a-python/commit/ecb321a354d691ca90b52cc39e0a397a576fd7d7))


### Bug Fixes

* add proper a2a request body documentation to Swagger UI ([#276](https://github.com/a2aproject/a2a-python/issues/276)) ([4343be9](https://github.com/a2aproject/a2a-python/commit/4343be99ad0df5eb6908867b71d55b1f7d0fafc6)), closes [#274](https://github.com/a2aproject/a2a-python/issues/274)
* Handle asyncio.cancellederror and raise to propagate back ([#293](https://github.com/a2aproject/a2a-python/issues/293)) ([9d6cb68](https://github.com/a2aproject/a2a-python/commit/9d6cb68a1619960b9c9fd8e7aa08ffb27047343f))
* Improve error handling in task creation ([#294](https://github.com/a2aproject/a2a-python/issues/294)) ([6412c75](https://github.com/a2aproject/a2a-python/commit/6412c75413e26489bd3d33f59e41b626a71807d3))
* Resolve dependency issue with sql stores ([#303](https://github.com/a2aproject/a2a-python/issues/303)) ([2126828](https://github.com/a2aproject/a2a-python/commit/2126828b5cb6291f47ca15d56c0e870950f17536))
* Send push notifications for message/send ([#298](https://github.com/a2aproject/a2a-python/issues/298)) ([0274112](https://github.com/a2aproject/a2a-python/commit/0274112bb5b077c17b344da3a65277f2ad67d38f))
* **server:** Improve event consumer error handling ([#282](https://github.com/a2aproject/a2a-python/issues/282)) ([a5786a1](https://github.com/a2aproject/a2a-python/commit/a5786a112779a21819d28e4dfee40fa11f1bb49a))

## [0.2.11](https://github.com/a2aproject/a2a-python/compare/v0.2.10...v0.2.11) (2025-07-07)


### ⚠ BREAKING CHANGES

* Removes `push_notifier` interface from the SDK and introduces `push_notification_config_store` and `push_notification_sender` for supporting push notifications.

### Features

* Add constants for Well-Known URIs ([#271](https://github.com/a2aproject/a2a-python/issues/271)) ([1c8e12e](https://github.com/a2aproject/a2a-python/commit/1c8e12e448dc7469e508fccdac06818836f5b520))
* Adds support for List and Delete push notification configurations. ([f1b576e](https://github.com/a2aproject/a2a-python/commit/f1b576e061e7a3ab891d8368ade56c7046684c5e))
* Adds support for more than one `push_notification_config` per task. ([f1b576e](https://github.com/a2aproject/a2a-python/commit/f1b576e061e7a3ab891d8368ade56c7046684c5e))
* **server:** Add lock to TaskUpdater to prevent race conditions ([#279](https://github.com/a2aproject/a2a-python/issues/279)) ([1022093](https://github.com/a2aproject/a2a-python/commit/1022093110100da27f040be4b35831bf8b1fe094))
* Support for database backend Task Store ([#259](https://github.com/a2aproject/a2a-python/issues/259)) ([7c46e70](https://github.com/a2aproject/a2a-python/commit/7c46e70b3142f3ec274c492bacbfd6e8f0204b36))


### Code Refactoring

* Removes `push_notifier` interface from the SDK and introduces `push_notification_config_store` and `push_notification_sender` for supporting push notifications. ([f1b576e](https://github.com/a2aproject/a2a-python/commit/f1b576e061e7a3ab891d8368ade56c7046684c5e))

## [0.2.10](https://github.com/a2aproject/a2a-python/compare/v0.2.9...v0.2.10) (2025-06-30)


### ⚠ BREAKING CHANGES

* Update to A2A Spec Version [0.2.5](https://github.com/a2aproject/A2A/releases/tag/v0.2.5) ([#197](https://github.com/a2aproject/a2a-python/issues/197))

### Features

* Add `append` and `last_chunk` to `add_artifact` method on `TaskUpdater` ([#186](https://github.com/a2aproject/a2a-python/issues/186)) ([8c6560f](https://github.com/a2aproject/a2a-python/commit/8c6560fd403887fab9d774bfcc923a5f6f459364))
* add a2a routes to existing app ([#188](https://github.com/a2aproject/a2a-python/issues/188)) ([32fecc7](https://github.com/a2aproject/a2a-python/commit/32fecc7194a61c2f5be0b8795d5dc17cdbab9040))
* Add middleware to the client SDK ([#171](https://github.com/a2aproject/a2a-python/issues/171)) ([efaabd3](https://github.com/a2aproject/a2a-python/commit/efaabd3b71054142109b553c984da1d6e171db24))
* Add more task state management methods to TaskUpdater ([#208](https://github.com/a2aproject/a2a-python/issues/208)) ([2b3bf6d](https://github.com/a2aproject/a2a-python/commit/2b3bf6d53ac37ed93fc1b1c012d59c19060be000))
* raise error for tasks in terminal states ([#215](https://github.com/a2aproject/a2a-python/issues/215)) ([a0bf13b](https://github.com/a2aproject/a2a-python/commit/a0bf13b208c90b439b4be1952c685e702c4917a0))

### Bug Fixes

* `consume_all` doesn't catch `asyncio.TimeoutError` in python 3.10 ([#216](https://github.com/a2aproject/a2a-python/issues/216)) ([39307f1](https://github.com/a2aproject/a2a-python/commit/39307f15a1bb70eb77aee2211da038f403571242))
* Append metadata and context id when processing TaskStatusUpdateE… ([#238](https://github.com/a2aproject/a2a-python/issues/238)) ([e106020](https://github.com/a2aproject/a2a-python/commit/e10602033fdd4f4e6b61af717ffc242d772545b3))
* Fix reference to `grpc.aio.ServicerContext` ([#237](https://github.com/a2aproject/a2a-python/issues/237)) ([0c1987b](https://github.com/a2aproject/a2a-python/commit/0c1987bb85f3e21089789ee260a0c62ac98b66a5))
* Fixes Short Circuit clause for context ID ([#236](https://github.com/a2aproject/a2a-python/issues/236)) ([a5509e6](https://github.com/a2aproject/a2a-python/commit/a5509e6b37701dfb5c729ccc12531e644a12f8ae))
* Resolve `APIKeySecurityScheme` parsing failed ([#226](https://github.com/a2aproject/a2a-python/issues/226)) ([aa63b98](https://github.com/a2aproject/a2a-python/commit/aa63b982edc2a07fd0df0b01fb9ad18d30b35a79))
* send notifications on message not streaming ([#219](https://github.com/a2aproject/a2a-python/issues/219)) ([91539d6](https://github.com/a2aproject/a2a-python/commit/91539d69e5c757712c73a41ab95f1ec6656ef5cd)), closes [#218](https://github.com/a2aproject/a2a-python/issues/218)

## [0.2.9](https://github.com/a2aproject/a2a-python/compare/v0.2.8...v0.2.9) (2025-06-24)

### Bug Fixes

* Set `protobuf==5.29.5` and `fastapi>=0.115.2` to prevent version conflicts ([#224](https://github.com/a2aproject/a2a-python/issues/224)) ([1412a85](https://github.com/a2aproject/a2a-python/commit/1412a855b4980d8373ed1cea38c326be74069633))

## [0.2.8](https://github.com/a2aproject/a2a-python/compare/v0.2.7...v0.2.8) (2025-06-12)


### Features

* Add HTTP Headers to ServerCallContext for Improved Handler Access ([#182](https://github.com/a2aproject/a2a-python/issues/182)) ([d5e5f5f](https://github.com/a2aproject/a2a-python/commit/d5e5f5f7e7a3cab7de13cff545a874fc58d85e46))
* Update A2A types from specification 🤖 ([#191](https://github.com/a2aproject/a2a-python/issues/191)) ([174230b](https://github.com/a2aproject/a2a-python/commit/174230bf6dfb6bf287d233a101b98cc4c79cad19))


### Bug Fixes

* Add `protobuf==6.31.1` to dependencies ([#189](https://github.com/a2aproject/a2a-python/issues/189)) ([ae1c31c](https://github.com/a2aproject/a2a-python/commit/ae1c31c1da47f6965c02e0564dc7d3791dd03e2c)), closes [#185](https://github.com/a2aproject/a2a-python/issues/185)

## [0.2.7](https://github.com/a2aproject/a2a-python/compare/v0.2.6...v0.2.7) (2025-06-11)


### Features

* Update A2A types from specification 🤖 ([#179](https://github.com/a2aproject/a2a-python/issues/179)) ([3ef4240](https://github.com/a2aproject/a2a-python/commit/3ef42405f6096281fe90b1df399731bd009bde12))

## [0.2.6](https://github.com/a2aproject/a2a-python/compare/v0.2.5...v0.2.6) (2025-06-09)


### ⚠ BREAKING CHANGES

* Add FastAPI JSONRPC Application ([#104](https://github.com/a2aproject/a2a-python/issues/104))

### Features

* Add FastAPI JSONRPC Application ([#104](https://github.com/a2aproject/a2a-python/issues/104)) ([0e66e1f](https://github.com/a2aproject/a2a-python/commit/0e66e1f81f98d7e2cf50b1c100e35d13ad7149dc))
* Add gRPC server and client support ([#162](https://github.com/a2aproject/a2a-python/issues/162)) ([a981605](https://github.com/a2aproject/a2a-python/commit/a981605dbb32e87bd241b64bf2e9bb52831514d1))
* add reject method to task_updater ([#147](https://github.com/a2aproject/a2a-python/issues/147)) ([2a6ef10](https://github.com/a2aproject/a2a-python/commit/2a6ef109f8b743f8eb53d29090cdec7df143b0b4))
* Add timestamp to `TaskStatus` updates on `TaskUpdater` ([#140](https://github.com/a2aproject/a2a-python/issues/140)) ([0c9df12](https://github.com/a2aproject/a2a-python/commit/0c9df125b740b947b0e4001421256491b5f87920))
* **spec:** Add an optional iconUrl field to the AgentCard 🤖 ([a1025f4](https://github.com/a2aproject/a2a-python/commit/a1025f406acd88e7485a5c0f4dd8a42488c41fa2))


### Bug Fixes

* Correctly adapt starlette BaseUser to A2A User ([#133](https://github.com/a2aproject/a2a-python/issues/133)) ([88d45eb](https://github.com/a2aproject/a2a-python/commit/88d45ebd935724e6c3ad614bf503defae4de5d85))
* Event consumer should stop on input_required ([#167](https://github.com/a2aproject/a2a-python/issues/167)) ([51c2d8a](https://github.com/a2aproject/a2a-python/commit/51c2d8addf9e89a86a6834e16deb9f4ac0e05cc3))
* Fix Release Version ([#161](https://github.com/a2aproject/a2a-python/issues/161)) ([011d632](https://github.com/a2aproject/a2a-python/commit/011d632b27b201193813ce24cf25e28d1335d18e))
* generate StrEnum types for enums ([#134](https://github.com/a2aproject/a2a-python/issues/134)) ([0c49dab](https://github.com/a2aproject/a2a-python/commit/0c49dabcdb9d62de49fda53d7ce5c691b8c1591c))
* library should released as 0.2.6 ([d8187e8](https://github.com/a2aproject/a2a-python/commit/d8187e812d6ac01caedf61d4edaca522e583d7da))
* remove error types from enqueable events ([#138](https://github.com/a2aproject/a2a-python/issues/138)) ([511992f](https://github.com/a2aproject/a2a-python/commit/511992fe585bd15e956921daeab4046dc4a50a0a))
* **stream:** don't block event loop in EventQueue ([#151](https://github.com/a2aproject/a2a-python/issues/151)) ([efd9080](https://github.com/a2aproject/a2a-python/commit/efd9080b917c51d6e945572fd123b07f20974a64))
* **task_updater:** fix potential duplicate artifact_id from default v… ([#156](https://github.com/a2aproject/a2a-python/issues/156)) ([1f0a769](https://github.com/a2aproject/a2a-python/commit/1f0a769c1027797b2f252e4c894352f9f78257ca))


### Documentation

* remove final and metadata fields from docstring ([#66](https://github.com/a2aproject/a2a-python/issues/66)) ([3c50ee1](https://github.com/a2aproject/a2a-python/commit/3c50ee1f64c103a543c8afb6d2ac3a11063b0f43))
* Update Links to Documentation Site ([5e7d418](https://github.com/a2aproject/a2a-python/commit/5e7d4180f7ae0ebeb76d976caa5ef68b4277ce54))

## [0.2.5](https://github.com/a2aproject/a2a-python/compare/v0.2.4...v0.2.5) (2025-05-27)


### Features

* Add a User representation to ServerCallContext ([#116](https://github.com/a2aproject/a2a-python/issues/116)) ([2cc2a0d](https://github.com/a2aproject/a2a-python/commit/2cc2a0de93631aa162823d43fe488173ed8754dc))
* Add functionality for extended agent card.  ([#31](https://github.com/a2aproject/a2a-python/issues/31)) ([20f0826](https://github.com/a2aproject/a2a-python/commit/20f0826a2cb9b77b89b85189fd91e7cd62318a30))
* Introduce a ServerCallContext ([#94](https://github.com/a2aproject/a2a-python/issues/94)) ([85b521d](https://github.com/a2aproject/a2a-python/commit/85b521d8a790dacb775ef764a66fbdd57b180da3))


### Bug Fixes

* fix hello world example for python 3.12 ([#98](https://github.com/a2aproject/a2a-python/issues/98)) ([536e4a1](https://github.com/a2aproject/a2a-python/commit/536e4a11f2f32332968a06e7d0bc4615e047a56c))
* Remove unused dependencies and update py version ([#119](https://github.com/a2aproject/a2a-python/issues/119)) ([9f8bc02](https://github.com/a2aproject/a2a-python/commit/9f8bc023b45544942583818968f3d320e5ff1c3b))
* Update hello world test client to match sdk behavior. Also down-level required python version ([#117](https://github.com/a2aproject/a2a-python/issues/117)) ([04c7c45](https://github.com/a2aproject/a2a-python/commit/04c7c452f5001d69524d94095d11971c1e857f75))
* Update the google adk demos to use ADK v1.0 ([#95](https://github.com/a2aproject/a2a-python/issues/95)) ([c351656](https://github.com/a2aproject/a2a-python/commit/c351656a91c37338668b0cd0c4db5fedd152d743))


### Documentation

* Update README for Python 3.10+ support ([#90](https://github.com/a2aproject/a2a-python/issues/90)) ([e0db20f](https://github.com/a2aproject/a2a-python/commit/e0db20ffc20aa09ee68304cc7e2a67c32ecdd6a8))

## [0.2.4](https://github.com/a2aproject/a2a-python/compare/v0.2.3...v0.2.4) (2025-05-22)

### Features

* Update to support python 3.10 ([#85](https://github.com/a2aproject/a2a-python/issues/85)) ([fd9c3b5](https://github.com/a2aproject/a2a-python/commit/fd9c3b5b0bbef509789a701171d95f690c84750b))


### Bug Fixes

* Throw exception for task_id mismatches ([#70](https://github.com/a2aproject/a2a-python/issues/70)) ([a9781b5](https://github.com/a2aproject/a2a-python/commit/a9781b589075280bfaaab5742d8b950916c9de74))

## [0.2.3](https://github.com/a2aproject/a2a-python/compare/v0.2.2...v0.2.3) (2025-05-20)


### Features

* Add request context builder with referenceTasks ([#56](https://github.com/a2aproject/a2a-python/issues/56)) ([f20bfe7](https://github.com/a2aproject/a2a-python/commit/f20bfe74b8cc854c9c29720b2ea3859aff8f509e))

## [0.2.2](https://github.com/a2aproject/a2a-python/compare/v0.2.1...v0.2.2) (2025-05-20)


### Documentation

* Write/Update Docstrings for Classes/Methods ([#59](https://github.com/a2aproject/a2a-python/issues/59)) ([9f773ef](https://github.com/a2aproject/a2a-python/commit/9f773eff4dddc4eec723d519d0050f21b9ccc042))
