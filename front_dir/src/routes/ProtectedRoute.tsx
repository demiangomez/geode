import { Navigate, Outlet } from "react-router-dom";

import { Toast } from "@componentsReact";
import { Layout } from "@pagesReact";

import { useUser, useAuth } from "@hooks";

import { apiMethods } from "@utils";

export const ProtectedRoute = () => {
    const { token } = useAuth();

    const {
        state: {
            status: userFetchStatus,
            method: userFetchMethod,
            msg: userMsg,
        },
    } = useUser();

    let msg = null;

    if (
        userFetchStatus === "unAuthorized" &&
        apiMethods.includes(userFetchMethod)
    ) {
        msg = <Toast error={true} msg={userMsg} />;
    }
    return token ? (
        <Layout>
            {userFetchStatus === "unAuthorized" && msg}
            <Outlet />
        </Layout>
    ) : (
        <Navigate to="/auth/login" />
    );
};
export default ProtectedRoute;
