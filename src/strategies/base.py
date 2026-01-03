class Strategy:
    name = "Base"

    def indicators(self, price):
        return {}

    def signals(self, price, indicators):
        raise NotImplementedError
