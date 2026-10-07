// SPDX-License-Identifier: MIT
pragma solidity ^0.8.30;
// Test-only token deployed to an ephemeral private EVM. Not production currency.
contract PaymentTestToken {
    string public constant name = "A2N Test Token";
    string public constant version = "1";
    mapping(address => uint256) public balanceOf;
    mapping(address => mapping(bytes32 => bool)) public authorizationState;
    bytes32 private immutable DOMAIN_SEPARATOR;
    bytes32 private constant TRANSFER_TYPEHASH = keccak256("TransferWithAuthorization(address from,address to,uint256 value,uint256 validAfter,uint256 validBefore,bytes32 nonce)");
    event Transfer(address indexed from, address indexed to, uint256 value);
    event AuthorizationUsed(address indexed authorizer, bytes32 indexed nonce);
    constructor() {
        DOMAIN_SEPARATOR = keccak256(abi.encode(
            keccak256("EIP712Domain(string name,string version,uint256 chainId,address verifyingContract)"),
            keccak256(bytes(name)), keccak256(bytes(version)), block.chainid, address(this)));
    }
    function mint(address to, uint256 value) external {
        balanceOf[to] += value;
        emit Transfer(address(0),to,value);
    }
    function transferWithAuthorization(address from,address to,uint256 value,uint256 validAfter,uint256 validBefore,
                                       bytes32 nonce,uint8 v,bytes32 r,bytes32 s) external {
        require(block.timestamp>validAfter && block.timestamp<validBefore,"time");
        require(!authorizationState[from][nonce],"used");
        bytes32 structHash=keccak256(abi.encode(TRANSFER_TYPEHASH,from,to,value,validAfter,validBefore,nonce));
        address recovered=ecrecover(keccak256(abi.encodePacked(hex"1901",DOMAIN_SEPARATOR,structHash)),v,r,s);
        require(recovered!=address(0) && recovered==from,"signature");
        require(balanceOf[from]>=value,"balance");
        authorizationState[from][nonce]=true;
        balanceOf[from]-=value;
        balanceOf[to]+=value;
        emit AuthorizationUsed(from,nonce);
        emit Transfer(from,to,value);
    }
}
