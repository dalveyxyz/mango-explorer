# # ⚠ Warning
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT
# LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN
# NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY,
# WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE
# SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#
# [🥭 Mango Markets](https://mango.markets/) support is available at:
#   [Docs](https://docs.mango.markets/)
#   [Discord](https://discord.gg/67jySBhxrg)
#   [Twitter](https://twitter.com/mangomarkets)
#   [Github](https://github.com/blockworks-foundation)
#   [Email](mailto:hello@blockworks.foundation)

import enum
import mango
import typing

from solana.publickey import PublicKey

from .modelstate import ModelState
from .modelstatebuilder import ModelStateBuilder, WebsocketModelStateBuilder, SerumPollingModelStateBuilder, SpotPollingModelStateBuilder, PerpPollingModelStateBuilder


class ModelUpdateMode(enum.Enum):
    # We use strings here so that argparse can work with these as parameters.
    WEBSOCKET = "WEBSOCKET"
    POLL = "POLL"

    def __str__(self) -> str:
        return self.value

    def __repr__(self) -> str:
        return f"{self}"


# # 🥭 ModelStateBuilder class
#
# Base class for building a `ModelState` through polling or websockets.
#
def model_state_builder_factory(mode: ModelUpdateMode, context: mango.Context, disposer: mango.DisposePropagator,
                                websocket_manager: mango.WebSocketSubscriptionManager, health_check: mango.HealthCheck,
                                wallet: mango.Wallet, group: mango.Group, account: mango.Account,
                                market: mango.Market, oracle: mango.Oracle) -> ModelStateBuilder:
    if mode == ModelUpdateMode.WEBSOCKET:
        return _websocket_model_state_builder_factory(context, disposer, websocket_manager, health_check, wallet, group, account, market, oracle)
    else:
        return _polling_model_state_builder_factory(context, wallet, group, account, market, oracle)


def _polling_model_state_builder_factory(context: mango.Context, wallet: mango.Wallet, group: mango.Group,
                                         account: mango.Account, market: mango.Market,
                                         oracle: mango.Oracle) -> ModelStateBuilder:
    if isinstance(market, mango.SerumMarket):
        return _polling_serum_model_state_builder_factory(context, wallet, group, account, market, oracle)
    elif isinstance(market, mango.SpotMarket):
        return _polling_spot_model_state_builder_factory(group, account, market, oracle)
    elif isinstance(market, mango.PerpMarket):
        return _polling_perp_model_state_builder_factory(group, account, market, oracle)
    else:
        raise Exception(f"Could not determine type of market {market.symbol}")


def _polling_serum_model_state_builder_factory(context: mango.Context, wallet: mango.Wallet, group: mango.Group,
                                               account: mango.Account, market: mango.SerumMarket,
                                               oracle: mango.Oracle) -> ModelStateBuilder:
    base_account = mango.TokenAccount.fetch_largest_for_owner_and_token(
        context, wallet.address, market.base)
    if base_account is None:
        raise Exception(
            f"Could not find token account owned by {wallet.address} for base token {market.base}.")
    quote_account = mango.TokenAccount.fetch_largest_for_owner_and_token(
        context, wallet.address, market.quote)
    if quote_account is None:
        raise Exception(
            f"Could not find token account owned by {wallet.address} for quote token {market.quote}.")
    all_open_orders = mango.OpenOrders.load_for_market_and_owner(
        context, market.address, wallet.address, context.dex_program_id, market.base.decimals, market.quote.decimals)
    if len(all_open_orders) == 0:
        raise Exception(
            f"Could not find serum openorders account owned by {wallet.address} for market {market.symbol}.")
    return SerumPollingModelStateBuilder(
        market, oracle, group.address, account.address, all_open_orders[0].address, base_account, quote_account)


def _polling_spot_model_state_builder_factory(group: mango.Group, account: mango.Account, market: mango.SpotMarket,
                                              oracle: mango.Oracle) -> ModelStateBuilder:
    market_index: int = group.find_spot_market_index(market.address)
    open_orders_address: typing.Optional[PublicKey] = account.spot_open_orders[market_index]
    if open_orders_address is None:
        raise Exception(
            f"Could not find spot openorders in account {account.address} for market {market.symbol}.")
    return SpotPollingModelStateBuilder(
        market, oracle, group.address, account.address, open_orders_address)


def _polling_perp_model_state_builder_factory(group: mango.Group, account: mango.Account, market: mango.PerpMarket,
                                              oracle: mango.Oracle) -> ModelStateBuilder:
    return PerpPollingModelStateBuilder(market, oracle, group.address, account.address)


def _websocket_model_state_builder_factory(context: mango.Context, disposer: mango.DisposePropagator,
                                           websocket_manager: mango.WebSocketSubscriptionManager,
                                           health_check: mango.HealthCheck, wallet: mango.Wallet,
                                           group: mango.Group, account: mango.Account, market: mango.Market,
                                           oracle: mango.Oracle) -> ModelStateBuilder:
    latest_group_observer = mango.build_group_watcher(context, websocket_manager, health_check, group)
    account_subscription, latest_account_observer = mango.build_account_watcher(
        context, websocket_manager, health_check, account, latest_group_observer)

    initial_price = oracle.fetch_price(context)
    price_feed = oracle.to_streaming_observable(context)
    latest_price_observer = mango.LatestItemObserverSubscriber(initial_price)
    price_disposable = price_feed.subscribe(latest_price_observer)
    disposer.add_disposable(price_disposable)
    health_check.add("price_subscription", price_feed)

    market = mango.ensure_market_loaded(context, market)
    if isinstance(market, mango.SerumMarket):
        inventory_watcher: mango.Watcher[mango.Inventory] = mango.build_serum_inventory_watcher(
            context, websocket_manager, health_check, disposer, wallet, market)
        latest_open_orders_observer: mango.Watcher[mango.PlacedOrdersContainer] = mango.build_serum_open_orders_watcher(
            context, websocket_manager, health_check, market, wallet)
        latest_bids_watcher: mango.Watcher[typing.Sequence[mango.Order]] = mango.build_serum_orderbook_side_watcher(
            context, websocket_manager, health_check, market.underlying_serum_market, mango.OrderBookSideType.BIDS)
        latest_asks_watcher: mango.Watcher[typing.Sequence[mango.Order]] = mango.build_serum_orderbook_side_watcher(
            context, websocket_manager, health_check, market.underlying_serum_market, mango.OrderBookSideType.ASKS)
    elif isinstance(market, mango.SpotMarket):
        inventory_watcher = mango.SpotInventoryAccountWatcher(market, latest_account_observer)
        latest_open_orders_observer = mango.build_spot_open_orders_watcher(
            context, websocket_manager, health_check, wallet, account, group, market)
        latest_bids_watcher = mango.build_serum_orderbook_side_watcher(
            context, websocket_manager, health_check, market.underlying_serum_market, mango.OrderBookSideType.BIDS)
        latest_asks_watcher = mango.build_serum_orderbook_side_watcher(
            context, websocket_manager, health_check, market.underlying_serum_market, mango.OrderBookSideType.ASKS)
    elif isinstance(market, mango.PerpMarket):
        inventory_watcher = mango.PerpInventoryAccountWatcher(market, latest_account_observer, group)
        latest_open_orders_observer = mango.build_perp_open_orders_watcher(
            context, websocket_manager, health_check, market, account, group, account_subscription)
        latest_bids_watcher = mango.build_perp_orderbook_side_watcher(
            context, websocket_manager, health_check, market, mango.OrderBookSideType.BIDS)
        latest_asks_watcher = mango.build_perp_orderbook_side_watcher(
            context, websocket_manager, health_check, market, mango.OrderBookSideType.ASKS)
    else:
        raise Exception(f"Could not determine type of market {market.symbol}")

    model_state = ModelState(market, latest_group_observer, latest_account_observer,
                             latest_price_observer, latest_open_orders_observer,
                             inventory_watcher, latest_bids_watcher, latest_asks_watcher)
    return WebsocketModelStateBuilder(model_state)